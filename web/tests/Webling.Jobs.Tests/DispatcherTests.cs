using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

namespace Webling.Jobs.Tests;

public sealed class DispatcherTests
{
    [Fact]
    public async Task Two_dispatchers_never_claim_the_same_job()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, Enumerable.Range(1, 60).ToArray());

        var recorder = new Recorder();
        void Handlers(IServiceCollection services) => services
            .AddSingleton(recorder)
            .AddJobHandler<TestJob, RecordingHandler>();
        using var first = database.Host(configure: Handlers);
        using var second = database.Host(configure: Handlers);
        await Task.WhenAll(first.StartAsync(), second.StartAsync());

        var jobs = await database.WaitForAsync(jobs => jobs.All(job => job.Status == JobStatus.Done));
        await Task.WhenAll(first.StopAsync(), second.StopAsync());

        Assert.Equal(60, recorder.Runs.Count);
        Assert.All(recorder.Runs.Values, runs => Assert.Equal(1, runs));
        Assert.All(jobs, job => Assert.Equal(1, job.Attempts));
    }

    [Fact]
    public async Task A_failure_retries_with_exponential_backoff_capped_at_the_maximum()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, 1);
        using var host = database.Host(
            options =>
            {
                options.BaseRetryDelay = TimeSpan.FromMinutes(40);
                options.MaxRetryDelay = TimeSpan.FromHours(1);
            },
            services => services.AddJobHandler<TestJob, FailingHandler>());
        await host.StartAsync();

        // First failure: 40 min, plus up to 20% jitter.
        var before = DateTimeOffset.UtcNow;
        var job = (await database.WaitForAsync(jobs => jobs[0].Attempts == 1 && jobs[0].Status == JobStatus.Pending))[0];
        AssertDelay(before, job.RunAfter, TimeSpan.FromMinutes(40));
        Assert.Contains("Job 1 broke.", job.LastError, StringComparison.Ordinal);

        // Second failure: 80 min, capped at 1 h, plus up to 20% jitter.
        await MakeDueAsync(database);
        before = DateTimeOffset.UtcNow;
        job = (await database.WaitForAsync(jobs => jobs[0].Attempts == 2 && jobs[0].Status == JobStatus.Pending))[0];
        AssertDelay(before, job.RunAfter, TimeSpan.FromHours(1));

        await host.StopAsync();
    }

    [Fact]
    public async Task The_default_backoff_starts_at_30_seconds_and_doubles()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, 1);
        using var host = database.Host(configure: services => services.AddJobHandler<TestJob, FailingHandler>());
        await host.StartAsync();

        var before = DateTimeOffset.UtcNow;
        var job = (await database.WaitForAsync(jobs => jobs[0].Attempts == 1 && jobs[0].Status == JobStatus.Pending))[0];
        AssertDelay(before, job.RunAfter, TimeSpan.FromSeconds(30));

        await MakeDueAsync(database);
        before = DateTimeOffset.UtcNow;
        job = (await database.WaitForAsync(jobs => jobs[0].Attempts == 2 && jobs[0].Status == JobStatus.Pending))[0];
        AssertDelay(before, job.RunAfter, TimeSpan.FromMinutes(1));

        await host.StopAsync();
    }

    [Fact]
    public async Task A_job_fails_after_the_last_attempt_and_keeps_its_error()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, 7);
        using var host = database.Host(
            options =>
            {
                options.MaxAttempts = 3;
                options.BaseRetryDelay = TimeSpan.Zero;
            },
            services => services.AddJobHandler<TestJob, FailingHandler>());
        await host.StartAsync();

        var job = (await database.WaitForAsync(jobs => jobs[0].Status == JobStatus.Failed))[0];
        await host.StopAsync();

        Assert.Equal(3, job.Attempts);
        Assert.Null(job.LockedUntil);
        Assert.Equal("System.InvalidOperationException: Job 7 broke.", job.LastError);
    }

    [Fact]
    public async Task A_job_whose_lease_expired_is_claimed_again()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, 1);

        // As if a worker claimed it and crashed: running, one attempt used, lease over.
        await using (var context = database.Context())
        {
            await context.Set<Job>().ExecuteUpdateAsync(setters => setters
                .SetProperty(job => job.Status, JobStatus.Running)
                .SetProperty(job => job.Attempts, 1)
                .SetProperty(job => job.LockedUntil, DateTimeOffset.UtcNow.AddMinutes(-1)));
        }

        var recorder = new Recorder();
        using var host = database.Host(configure: services => services
            .AddSingleton(recorder)
            .AddJobHandler<TestJob, RecordingHandler>());
        await host.StartAsync();

        var job = (await database.WaitForAsync(jobs => jobs[0].Status == JobStatus.Done))[0];
        await host.StopAsync();

        Assert.Equal(2, job.Attempts);
        Assert.Equal(1, recorder.Runs[1]);
    }

    [Fact]
    public async Task A_running_job_with_a_live_lease_is_not_claimed()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, 1);
        await using (var context = database.Context())
        {
            await context.Set<Job>().ExecuteUpdateAsync(setters => setters
                .SetProperty(job => job.Status, JobStatus.Running)
                .SetProperty(job => job.Attempts, 1)
                .SetProperty(job => job.LockedUntil, DateTimeOffset.UtcNow.AddMinutes(5)));
        }

        var recorder = new Recorder();
        using var host = database.Host(configure: services => services
            .AddSingleton(recorder)
            .AddJobHandler<TestJob, RecordingHandler>());
        await host.StartAsync();
        await Task.Delay(500);
        await host.StopAsync();

        Assert.Empty(recorder.Runs);
        Assert.Equal(JobStatus.Running, (await database.JobsAsync())[0].Status);
    }

    [Fact]
    public async Task Stopping_the_host_hands_the_job_back_with_its_attempt()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, 1);

        var recorder = new Recorder();
        using var host = database.Host(configure: services => services
            .AddSingleton(recorder)
            .AddJobHandler<TestJob, BlockingHandler>());
        await host.StartAsync();
        await recorder.Started.Task.WaitAsync(TimeSpan.FromSeconds(30));

        var stopping = DateTimeOffset.UtcNow;
        await host.StopAsync();

        var job = (await database.JobsAsync())[0];
        Assert.Equal(JobStatus.Pending, job.Status);
        Assert.Equal(0, job.Attempts);
        Assert.Null(job.LockedUntil);
        Assert.InRange(job.RunAfter, stopping.AddSeconds(-1), DateTimeOffset.UtcNow.AddSeconds(1));
    }

    [Fact]
    public async Task A_job_without_a_handler_fails_like_any_other()
    {
        await using var database = await TestDatabase.CreateAsync();
        await EnqueueAsync(database, 1);
        using var host = database.Host(options =>
        {
            options.MaxAttempts = 1;
        });
        await host.StartAsync();

        var job = (await database.WaitForAsync(jobs => jobs[0].Status == JobStatus.Failed))[0];
        await host.StopAsync();

        Assert.Contains("No handler is registered for job type 'test'", job.LastError, StringComparison.Ordinal);
    }

    private static async Task EnqueueAsync(TestDatabase database, params int[] numbers)
    {
        await using var context = database.Context();
        var now = DateTimeOffset.UtcNow.AddSeconds(-1);
        foreach (var number in numbers)
        {
            context.EnqueueJob(new TestJob(number), now);
        }

        await context.SaveChangesAsync();
    }

    private static async Task MakeDueAsync(TestDatabase database)
    {
        await using var context = database.Context();
        await context.Set<Job>().ExecuteUpdateAsync(setters => setters.SetProperty(job => job.RunAfter, DateTimeOffset.UtcNow.AddSeconds(-1)));
    }

    private static void AssertDelay(DateTimeOffset before, DateTimeOffset runAfter, TimeSpan delay) =>
        Assert.InRange(runAfter, before + delay, DateTimeOffset.UtcNow + (delay * 1.2));
}
