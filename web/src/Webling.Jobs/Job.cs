using System.Text.Json;

namespace Webling.Jobs;

/// <summary>
/// A unit of background work in the <c>jobs</c> table, claimed and run by the dispatcher. Map it with
/// <see cref="JobModelBuilderExtensions.ApplyWeblingJobs"/>.
/// </summary>
public sealed class Job
{
    /// <summary>
    /// The serializer options for payloads, shared by producers and the dispatcher.
    /// </summary>
    public static JsonSerializerOptions PayloadSerializerOptions { get; } = JsonSerializerOptions.Web;

    public Guid Id { get; set; }

    public required string Type { get; set; }

    public required string Payload { get; set; }

    public JobStatus Status { get; set; }

    public int Attempts { get; set; }

    public DateTimeOffset RunAfter { get; set; }

    public DateTimeOffset? LockedUntil { get; set; }

    public string? LastError { get; set; }

    public DateTimeOffset CreatedAt { get; set; }

    public DateTimeOffset UpdatedAt { get; set; }
}
