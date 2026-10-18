using System.Collections.Concurrent;

namespace Webling.Jobs.Tests;

/// <summary>
/// What the handlers saw, shared by every handler in a host.
/// </summary>
public sealed class Recorder
{
    public ConcurrentDictionary<int, int> Runs { get; } = new();

    public TaskCompletionSource Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
}

public sealed class RecordingHandler(Recorder recorder) : IJobHandler<TestJob>
{
    public async Task HandleAsync(TestJob job, CancellationToken cancellationToken)
    {
        recorder.Runs.AddOrUpdate(job.Number, 1, (_, runs) => runs + 1);
        await Task.Delay(5, cancellationToken);
    }
}

public sealed class FailingHandler : IJobHandler<TestJob>
{
    public Task HandleAsync(TestJob job, CancellationToken cancellationToken) =>
        throw new InvalidOperationException($"Job {job.Number} broke.");
}

public sealed class BlockingHandler(Recorder recorder) : IJobHandler<TestJob>
{
    public async Task HandleAsync(TestJob job, CancellationToken cancellationToken)
    {
        recorder.Started.TrySetResult();
        await Task.Delay(Timeout.Infinite, cancellationToken);
    }
}
