using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using OpenTelemetry;

namespace Webling.Jobs;

// Deletes done jobs older than KeepDone and failed jobs older than KeepFailed, by their last update, when the host
// starts and every CleanupInterval after. Replicas may run it at the same time; the deletes don't conflict.
internal sealed class JobCleanup<TContext>(
    IServiceScopeFactory scopeFactory,
    IOptions<JobDispatcherOptions> options,
    TimeProvider timeProvider,
    ILogger<JobCleanup<TContext>> logger) : BackgroundService
    where TContext : DbContext
{
    private readonly JobDispatcherOptions options = options.Value;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await RunOnceAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                return;
            }
            catch (Exception exception)
            {
                JobLog.CleanupFailed(logger, exception);
            }

            await Task.Delay(options.CleanupInterval, timeProvider, stoppingToken);
        }
    }

    public async Task<int> RunOnceAsync(CancellationToken cancellationToken)
    {
        using var suppressTracing = SuppressInstrumentationScope.Begin();
        await using var scope = scopeFactory.CreateAsyncScope();
        var jobs = scope.ServiceProvider.GetRequiredService<TContext>().Set<Job>();

        var now = timeProvider.GetUtcNow();
        var doneBefore = now - options.KeepDone;
        var failedBefore = now - options.KeepFailed;
        var deleted = await jobs
            .Where(job => (job.Status == JobStatus.Done && job.UpdatedAt < doneBefore)
                || (job.Status == JobStatus.Failed && job.UpdatedAt < failedBefore))
            .ExecuteDeleteAsync(cancellationToken);

        if (deleted > 0)
        {
            JobLog.CleanedUp(logger, deleted);
        }

        return deleted;
    }
}
