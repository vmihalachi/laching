using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace Webling.Jobs;

/// <summary>
/// Deserializes a payload and runs the registered handler for one job type.
/// </summary>
public sealed record JobHandlerRegistration(string JobType, Func<IServiceProvider, string, CancellationToken, Task> HandleAsync);

public static class JobServiceCollectionExtensions
{
    /// <summary>
    /// Runs jobs from the table mapped in <typeparamref name="TContext"/> one at a time, and deletes old finished
    /// jobs every <see cref="JobDispatcherOptions.CleanupInterval"/>. <paramref name="configuration"/>, such as the
    /// <c>Jobs</c> section, binds <see cref="JobDispatcherOptions"/>. The context must be registered as a scoped
    /// service, as <c>AddDbContext</c> does.
    /// </summary>
    public static IServiceCollection AddJobDispatcher<TContext>(this IServiceCollection services, IConfiguration? configuration = null)
        where TContext : DbContext
    {
        ArgumentNullException.ThrowIfNull(services);

        var options = services.AddOptions<JobDispatcherOptions>();
        if (configuration is not null)
        {
            options.Bind(configuration);
        }

        services.TryAddSingleton(TimeProvider.System);
        services.AddHostedService<JobDispatcher<TContext>>();
        services.AddHostedService<JobCleanup<TContext>>();

        return services;
    }

    public static IServiceCollection AddJobHandler<TJob, THandler>(this IServiceCollection services)
        where TJob : IJob
        where THandler : class, IJobHandler<TJob>
    {
        ArgumentNullException.ThrowIfNull(services);

        services.AddScoped<IJobHandler<TJob>, THandler>();
        services.AddSingleton(new JobHandlerRegistration(TJob.JobType, static (provider, payload, cancellationToken) =>
        {
            var job = JsonSerializer.Deserialize<TJob>(payload, Job.PayloadSerializerOptions)
                ?? throw new JsonException($"The payload of a '{TJob.JobType}' job is null.");

            return provider.GetRequiredService<IJobHandler<TJob>>().HandleAsync(job, cancellationToken);
        }));

        return services;
    }
}
