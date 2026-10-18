using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Webling.Cloudflare;

/// <summary>
/// Verifies a Turnstile token with Cloudflare's siteverify API. A token is valid once and for five minutes; a
/// missing, reused or failed token is refused. Network failures count as a failed check, never as a pass.
/// </summary>
public sealed partial class TurnstileCheck(HttpClient http, IOptions<TurnstileOptions> options, ILogger<TurnstileCheck> logger) : IHumanCheck
{
    public static readonly Uri VerifyUrl = new("https://challenges.cloudflare.com/turnstile/v0/siteverify");

    // Turnstile tokens are at most 2048 characters.
    private const int MaxTokenLength = 2048;

    public async Task<bool> VerifyAsync(HumanProof proof, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(proof);

        if (string.IsNullOrEmpty(proof.Token) || proof.Token.Length > MaxTokenLength)
        {
            return false;
        }

        var fields = new Dictionary<string, string>
        {
            ["secret"] = options.Value.SecretKey,
            ["response"] = proof.Token,
        };
        if (!string.IsNullOrEmpty(proof.RemoteIp))
        {
            fields["remoteip"] = proof.RemoteIp;
        }

        try
        {
            using var response = await http.PostAsync(VerifyUrl, new FormUrlEncodedContent(fields), cancellationToken);
            response.EnsureSuccessStatusCode();
            var result = await response.Content.ReadFromJsonAsync<VerifyResult>(cancellationToken);
            if (result is { Success: true })
            {
                return true;
            }

            LogRefused(logger, string.Join(',', result?.ErrorCodes ?? []));
            return false;
        }
        catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException or JsonException)
        {
            LogUnavailable(logger, exception);
            return false;
        }
    }

    private sealed record VerifyResult(
        [property: JsonPropertyName("success")] bool Success,
        [property: JsonPropertyName("error-codes")] string[]? ErrorCodes);

    [LoggerMessage(Level = LogLevel.Information, Message = "Turnstile refused a token: {ErrorCodes}")]
    private static partial void LogRefused(ILogger logger, string errorCodes);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Turnstile verification failed")]
    private static partial void LogUnavailable(ILogger logger, Exception exception);
}
