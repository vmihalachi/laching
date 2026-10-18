using System.Diagnostics;
using Aspire.Hosting;
using Aspire.Hosting.Pipelines;

namespace Webling.Aspire;

/// <summary>
/// Runs a deployed Container Apps job, such as database migrations, and waits for it to succeed before the apps are
/// provisioned. Aspire's <c>WaitForCompletion</c> orders local startup only; in Azure the job needs a deployment
/// step. Uses the Azure CLI, signed in as the deployment is.
/// </summary>
public static class ContainerAppJobDeployment
{
    /// <summary>
    /// Adds a deployment step, <c>run-{jobName}-job</c>, that runs after the job's Container App job is provisioned
    /// and before each of <paramref name="appNames"/> is, through <see cref="RunAsync"/>. The names are the Aspire
    /// resource names.
    /// </summary>
    public static IDistributedApplicationBuilder RunJobBeforeApps(
        this IDistributedApplicationBuilder builder,
        string jobName,
        string subscription,
        string resourceGroup,
        params string[] appNames)
    {
        ArgumentNullException.ThrowIfNull(builder);
        ArgumentException.ThrowIfNullOrEmpty(jobName);
        ArgumentNullException.ThrowIfNull(appNames);

        var stepName = $"run-{jobName}-job";
#pragma warning disable ASPIREPIPELINES001
        builder.Pipeline.AddStep(
            stepName,
            context => RunAsync(subscription, resourceGroup, jobName, context.CancellationToken),
            requiredBy: WellKnownPipelineSteps.Deploy);
        builder.Pipeline.AddPipelineConfiguration(context =>
        {
            // Azure adds the provision steps after its before-start preparation pass.
            var provisionJob = context.Steps.SingleOrDefault(step => step.Name == $"provision-{jobName}-containerapp");
            if (provisionJob is not null)
            {
                var run = context.Steps.Single(step => step.Name == stepName);
                if (!run.DependsOnSteps.Contains(provisionJob.Name))
                {
                    run.DependsOn(provisionJob);
                }

                foreach (var app in appNames)
                {
                    context.Steps.Single(step => step.Name == $"provision-{app}-containerapp").DependsOn(run);
                }
            }

            return Task.CompletedTask;
        });
#pragma warning restore ASPIREPIPELINES001

        return builder;
    }

    /// <summary>
    /// Starts the Container Apps job <paramref name="jobName"/> and waits up to ten minutes for its execution to
    /// succeed. Throws when it fails, stops or degrades.
    /// </summary>
    public static async Task RunAsync(string subscription, string resourceGroup, string jobName, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromMinutes(10));
        var token = timeout.Token;
        var common = new[] { "--subscription", subscription, "--resource-group", resourceGroup, "--name", jobName };
        var execution = await RunAzAsync(["containerapp", "job", "start", .. common, "--query", "name", "-o", "tsv"], token);
        if (string.IsNullOrWhiteSpace(execution))
        {
            throw new InvalidOperationException($"Azure did not return an execution of job {jobName}.");
        }

        while (true)
        {
            var status = await RunAzAsync(
            [
                "containerapp", "job", "execution", "show", .. common,
                "--job-execution-name", execution, "--query", "properties.status", "-o", "tsv",
            ], token);
            if (status == "Succeeded")
            {
                return;
            }

            if (status is "Failed" or "Stopped" or "Degraded")
            {
                throw new InvalidOperationException($"Job {jobName} execution {execution} ended with status {status}.");
            }

            await Task.Delay(TimeSpan.FromSeconds(5), token);
        }
    }

    // Runs Azure CLI within the overall deadline, reading both output streams and stopping it on cancellation.
    private static async Task<string> RunAzAsync(string[] arguments, CancellationToken cancellationToken)
    {
        var start = new ProcessStartInfo("az")
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
        };
        foreach (var argument in arguments)
        {
            start.ArgumentList.Add(argument);
        }

        using var process = Process.Start(start) ?? throw new InvalidOperationException("Cannot start Azure CLI.");
        var output = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var error = process.StandardError.ReadToEndAsync(cancellationToken);
        try
        {
            await process.WaitForExitAsync(cancellationToken);
        }
        catch (OperationCanceledException)
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }

            throw;
        }

        if (process.ExitCode != 0)
        {
            throw new InvalidOperationException($"Azure CLI failed: {await error}");
        }

        return (await output).Trim();
    }
}
