namespace Webling.Cloudflare;

/// <summary>
/// The Cloudflare zone whose cache <see cref="CloudflareCache"/> purges, bound from the <c>Cloudflare</c>
/// configuration section. Without all three values purging is off, as in local development.
/// </summary>
public sealed class CloudflareCacheOptions
{
    public const string SectionName = "Cloudflare";

    public string? ZoneId { get; set; }

    /// <summary>
    /// An API token limited to Cache Purge on the zone. Never logged.
    /// </summary>
    public string? PurgeToken { get; set; }

    /// <summary>
    /// The public origin the cached addresses start with, such as <c>https://www.example.com</c>.
    /// </summary>
    public string? PublicOrigin { get; set; }

    public bool IsConfigured =>
        !string.IsNullOrWhiteSpace(ZoneId) && !string.IsNullOrWhiteSpace(PurgeToken) && !string.IsNullOrWhiteSpace(PublicOrigin);
}
