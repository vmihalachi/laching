namespace Webling.Jobs;

/// <summary>
/// A job payload. The payload is stored as JSON in the job table and handled by the dispatcher.
/// </summary>
public interface IJob
{
    /// <summary>
    /// The stable name stored in the <c>type</c> column. Never rename it while jobs of this type
    /// may still be queued.
    /// </summary>
    static abstract string JobType { get; }
}
