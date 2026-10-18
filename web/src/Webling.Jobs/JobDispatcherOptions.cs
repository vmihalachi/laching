namespace Webling.Jobs;

public sealed class JobDispatcherOptions
{
    /// <summary>
    /// How long the dispatcher waits before polling again when no job is due.
    /// </summary>
    public TimeSpan PollInterval { get; set; } = TimeSpan.FromSeconds(2);

    /// <summary>
    /// How long a claimed job stays locked. The handler is cancelled when the lease ends, and
    /// another dispatcher may then claim the job again.
    /// </summary>
    public TimeSpan LeaseDuration { get; set; } = TimeSpan.FromMinutes(5);

    /// <summary>
    /// The number of attempts after which a failing job is marked failed.
    /// </summary>
    public int MaxAttempts { get; set; } = 5;

    /// <summary>
    /// The delay before the first retry. Each later retry doubles it, up to <see cref="MaxRetryDelay"/>.
    /// </summary>
    public TimeSpan BaseRetryDelay { get; set; } = TimeSpan.FromSeconds(30);

    public TimeSpan MaxRetryDelay { get; set; } = TimeSpan.FromHours(1);

    /// <summary>
    /// How long a <c>done</c> job is kept before the cleanup deletes it.
    /// </summary>
    public TimeSpan KeepDone { get; set; } = TimeSpan.FromDays(7);

    /// <summary>
    /// How long a <c>failed</c> job, with its last error, is kept before the cleanup deletes it.
    /// </summary>
    public TimeSpan KeepFailed { get; set; } = TimeSpan.FromDays(30);

    /// <summary>
    /// How often the cleanup runs, starting when the host starts.
    /// </summary>
    public TimeSpan CleanupInterval { get; set; } = TimeSpan.FromHours(1);
}
