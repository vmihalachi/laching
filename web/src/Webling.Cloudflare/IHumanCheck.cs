namespace Webling.Cloudflare;

/// <summary>
/// Proves that a public request comes from a person, not a script. Use cases that reveal contact data or store
/// visitor input call it before anything else. <see cref="TurnstileCheck"/> implements it with Cloudflare Turnstile.
/// </summary>
public interface IHumanCheck
{
    Task<bool> VerifyAsync(HumanProof proof, CancellationToken cancellationToken);
}

/// <summary>
/// The token the visitor's browser received from the check, and the visitor's address as Cloudflare saw it.
/// </summary>
public sealed record HumanProof(string? Token, string? RemoteIp);
