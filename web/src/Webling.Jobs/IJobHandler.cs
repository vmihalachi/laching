namespace Webling.Jobs;

/// <summary>
/// Handles jobs of one type. A job may run more than once (after a crash, a lost lease or a
/// retry), so handlers must be idempotent.
/// </summary>
public interface IJobHandler<in TJob>
    where TJob : IJob
{
    Task HandleAsync(TJob job, CancellationToken cancellationToken);
}
