namespace Webling.Cloudflare;

/// <summary>
/// Cloudflare Turnstile keys, bound from the <c>Turnstile</c> configuration section.
/// </summary>
public sealed class TurnstileOptions
{
    public const string SectionName = "Turnstile";

    /// <summary>
    /// Cloudflare's published test site key, whose widget always passes. For development only.
    /// </summary>
    public const string TestSiteKey = "1x00000000000000000000AA";

    /// <summary>
    /// Cloudflare's published test secret key, which accepts every token. For development only.
    /// </summary>
    public const string TestSecretKey = "1x0000000000000000000000000000000AA";

    /// <summary>
    /// The public site key, rendered into pages.
    /// </summary>
    public string SiteKey { get; set; } = "";

    /// <summary>
    /// The secret key for server-side verification. Never logged or rendered.
    /// </summary>
    public string SecretKey { get; set; } = "";
}
