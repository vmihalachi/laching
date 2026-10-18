using System.Diagnostics;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using OpenTelemetry;

namespace Webling.Jobs;

// Runs jobs from the job table one at a time. A claim takes the next due job with FOR UPDATE SKIP LOCKED, so
// dispatchers in several replicas never claim the same job, counts the attempt and leases the job until
// locked_until. A job whose lease expires, because its worker crashed or lost the database, becomes claimable
// again. Failures retry with exponential backoff until MaxAttempts, then the job is marked failed.
internal sealed class JobDispatcher<TContext>(
    IServiceScopeFactory scopeFactory,
    IEnumerable<JobHandlerRegistration> registrations,
    IOptions<JobDispatcherOptions> options,
    TimeProvider timeProvider,
    ILogger<JobDispatcher<TContext>> logger) : BackgroundService
    where TContext : DbContext
{
    private const int MaxErrorLength = 2000;

    private readonly Dictionary<string, JobHandlerRegistration> handlers =
        registrations.ToDictionary(registration => registration.JobType, StringComparer.Ordinal);

    private readonly JobDispatcherOptions options = options.Value;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            var ranJob = false;
            try
            {
                ranJob = await TryRunNextAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception exception)
            {
                JobLog.DispatchFailed(logger, exception);
            }

            if (!ranJob)
            {
                await Task.Delay(options.PollInterval, timeProvider, stoppingToken);
            }
        }
    }

    private async Task<bool> TryRunNextAsync(CancellationToken stoppingToken)
    {
        await using var scope = scopeFactory.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<TContext>();

        var job = await ClaimAsync(db, stoppingToken);
        if (job is null)
        {
            return false;
        }

        using var activity = JobLog.ActivitySource.StartActivity($"job {job.Type}");
        activity?.SetTag("job.id", job.Id);
        activity?.SetTag("job.type", job.Type);
        activity?.SetTag("job.attempt", job.Attempts);

        try
        {
            await RunHandlerAsync(job, stoppingToken);
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            // The host is stopping: hand the job back right away instead of waiting for the lease.
            await ReleaseAsync(db, job);
            throw;
        }
        catch (Exception exception)
        {
            activity?.AddException(exception);
            activity?.SetStatus(ActivityStatusCode.Error);
            await FailAsync(db, job, exception);
            return true;
        }

        await CompleteAsync(db, job);
        return true;
    }

    private async Task<Job?> ClaimAsync(TContext db, CancellationToken cancellationToken)
    {
        // Polling runs every few seconds; tracing it would bury the job traces.
        using var suppressTracing = SuppressInstrumentationScope.Begin();

        // EF Core runs this SQL verbatim only because nothing composes on it. Do not add Where,
        // Select or similar: EF would wrap the statement in a subquery, and PostgreSQL rejects a
        // data-modifying statement there. The names are the ones ApplyWeblingJobs maps.
        var claimed = await db.Set<Job>()
            .FromSql($"""
                UPDATE jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    locked_until = now() + {options.LeaseDuration},
                    updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM jobs
                    WHERE (status = 'pending' AND run_after <= now())
                       OR (status = 'running' AND locked_until < now())
                    ORDER BY run_after
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED)
                RETURNING *
                """)
            .AsNoTracking()
            .ToListAsync(cancellationToken);

        return claimed.SingleOrDefault();
    }

    private async Task RunHandlerAsync(Job job, CancellationToken stoppingToken)
    {
        // A job whose final attempt lost its lease is claimed once more only to be marked failed.
        if (job.Attempts > options.MaxAttempts)
        {
            throw new InvalidOperationException($"The lease expired during the final attempt ({options.MaxAttempts}).");
        }

        if (!handlers.TryGetValue(job.Type, out var registration))
        {
            throw new InvalidOperationException($"No handler is registered for job type '{job.Type}'.");
        }

        using var lease = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        lease.CancelAfter(options.LeaseDuration);

        await using var scope = scopeFactory.CreateAsyncScope();
        await registration.HandleAsync(scope.ServiceProvider, job.Payload, lease.Token);
    }

    private async Task CompleteAsync(TContext db, Job job)
    {
        var now = timeProvider.GetUtcNow();
        var updated = await ClaimedBy(db, job).ExecuteUpdateAsync(setters => setters
            .SetProperty(j => j.Status, JobStatus.Done)
            .SetProperty(j => j.LockedUntil, (DateTimeOffset?)null)
            .SetProperty(j => j.LastError, (string?)null)
            .SetProperty(j => j.UpdatedAt, now), CancellationToken.None);

        if (updated == 0)
        {
            JobLog.LeaseLost(logger, job.Id, job.Type);
            return;
        }

        JobLog.JobDone(logger, job.Id, job.Type, job.Attempts);
    }

    private async Task FailAsync(TContext db, Job job, Exception exception)
    {
        var now = timeProvider.GetUtcNow();
        var retry = job.Attempts < options.MaxAttempts;
        var runAfter = retry ? now + RetryDelay(job.Attempts) : job.RunAfter;
        var error = $"{exception.GetType().FullName}: {exception.Message}";
        if (error.Length > MaxErrorLength)
        {
            error = error[..MaxErrorLength];
        }

        var updated = await ClaimedBy(db, job).ExecuteUpdateAsync(setters => setters
            .SetProperty(j => j.Status, retry ? JobStatus.Pending : JobStatus.Failed)
            .SetProperty(j => j.RunAfter, runAfter)
            .SetProperty(j => j.LockedUntil, (DateTimeOffset?)null)
            .SetProperty(j => j.LastError, error)
            .SetProperty(j => j.UpdatedAt, now), CancellationToken.None);

        if (updated == 0)
        {
            JobLog.LeaseLost(logger, job.Id, job.Type);
        }
        else if (retry)
        {
            JobLog.JobRetrying(logger, exception, job.Id, job.Type, job.Attempts, runAfter);
        }
        else
        {
            JobLog.JobFailed(logger, exception, job.Id, job.Type, job.Attempts);
        }
    }

    // The host is stopping before the handler finished. Nothing failed, so the job keeps the
    // attempt it was claimed with and runs again as soon as a worker is back.
    private async Task ReleaseAsync(TContext db, Job job)
    {
        var now = timeProvider.GetUtcNow();
        await ClaimedBy(db, job).ExecuteUpdateAsync(setters => setters
            .SetProperty(j => j.Status, JobStatus.Pending)
            .SetProperty(j => j.Attempts, j => j.Attempts - 1)
            .SetProperty(j => j.RunAfter, now)
            .SetProperty(j => j.LockedUntil, (DateTimeOffset?)null)
            .SetProperty(j => j.UpdatedAt, now), CancellationToken.None);
    }

    // Matches the job only while this dispatcher still holds the claim. After a lease expires,
    // another claim increments the attempt count and this one no longer matches.
    private static IQueryable<Job> ClaimedBy(TContext db, Job job) =>
        db.Set<Job>().Where(j => j.Id == job.Id && j.Status == JobStatus.Running && j.Attempts == job.Attempts);

    // 30 s, 1 min, 2 min, 4 min... capped at MaxRetryDelay, each with up to 20% jitter so that
    // jobs that failed together do not retry together.
    private TimeSpan RetryDelay(int attempts)
    {
        var seconds = Math.Min(
            options.BaseRetryDelay.TotalSeconds * Math.Pow(2, attempts - 1),
            options.MaxRetryDelay.TotalSeconds);

        return TimeSpan.FromSeconds(seconds * (1 + (Random.Shared.NextDouble() * 0.2)));
    }
}
