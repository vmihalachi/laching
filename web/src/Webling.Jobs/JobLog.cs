using System.Diagnostics;
using Microsoft.Extensions.Logging;

namespace Webling.Jobs;

internal static partial class JobLog
{
    /// <summary>
    /// The source of one activity per job run, named <c>job {type}</c>. Add <c>Webling.Jobs</c> to the tracer to
    /// export them.
    /// </summary>
    public static readonly ActivitySource ActivitySource = new("Webling.Jobs");

    [LoggerMessage(Level = LogLevel.Information, Message = "Job {JobId} ({JobType}) done on attempt {Attempt}")]
    public static partial void JobDone(ILogger logger, Guid jobId, string jobType, int attempt);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Job {JobId} ({JobType}) failed on attempt {Attempt}; retrying after {RunAfter}")]
    public static partial void JobRetrying(ILogger logger, Exception exception, Guid jobId, string jobType, int attempt, DateTimeOffset runAfter);

    [LoggerMessage(Level = LogLevel.Error, Message = "Job {JobId} ({JobType}) failed after {Attempt} attempts")]
    public static partial void JobFailed(ILogger logger, Exception exception, Guid jobId, string jobType, int attempt);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Job {JobId} ({JobType}) lost its lease before it finished; its result was not recorded")]
    public static partial void LeaseLost(ILogger logger, Guid jobId, string jobType);

    [LoggerMessage(Level = LogLevel.Error, Message = "Job dispatch failed")]
    public static partial void DispatchFailed(ILogger logger, Exception exception);

    [LoggerMessage(Level = LogLevel.Information, Message = "Deleted {Count} finished jobs")]
    public static partial void CleanedUp(ILogger logger, int count);

    [LoggerMessage(Level = LogLevel.Error, Message = "Job cleanup failed")]
    public static partial void CleanupFailed(ILogger logger, Exception exception);
}
