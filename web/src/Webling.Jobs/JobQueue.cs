using System.Text.Json;
using Microsoft.EntityFrameworkCore;

namespace Webling.Jobs;

public static class JobQueue
{
    /// <summary>
    /// Adds a job to the context. It is queued when the caller saves changes, in the same
    /// transaction as the caller's other changes.
    /// </summary>
    public static Job EnqueueJob<TJob>(this DbContext db, TJob payload, DateTimeOffset now, DateTimeOffset? runAfter = null)
        where TJob : IJob
    {
        ArgumentNullException.ThrowIfNull(db);

        var job = new Job
        {
            Id = Guid.CreateVersion7(now),
            Type = TJob.JobType,
            Payload = JsonSerializer.Serialize(payload, Job.PayloadSerializerOptions),
            Status = JobStatus.Pending,
            RunAfter = runAfter ?? now,
            CreatedAt = now,
            UpdatedAt = now,
        };

        db.Set<Job>().Add(job);
        return job;
    }
}
