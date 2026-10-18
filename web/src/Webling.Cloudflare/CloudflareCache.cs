using System.Net.Http.Headers;
using System.Net.Http.Json;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Webling.Cloudflare;

/// <summary>
/// Purges single addresses from Cloudflare's cache through the API. The Free plan purges by address, at most 30
/// per request, so larger sets go out in batches. A refused purge throws, so a job can retry it.
/// </summary>
public sealed partial class CloudflareCache(HttpClient http, IOptions<CloudflareCacheOptions> options, ILogger<CloudflareCache> logger)
{
    public static readonly Uri ApiBase = new("https://api.cloudflare.com/client/v4/");

    private const int FilesPerRequest = 30;

    /// <summary>
    /// Purges <paramref name="paths"/> (such as <c>/en/about</c>) under the public origin. Does nothing when
    /// purging is not configured.
    /// </summary>
    public async Task PurgeAsync(IReadOnlyCollection<string> paths, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(paths);

        var settings = options.Value;
        if (!settings.IsConfigured)
        {
            LogDisabled(logger, paths.Count);
            return;
        }

        var origin = settings.PublicOrigin!.TrimEnd('/');
        foreach (var batch in paths.Distinct(StringComparer.Ordinal).Select(path => origin + path).Chunk(FilesPerRequest))
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, $"zones/{Uri.EscapeDataString(settings.ZoneId!)}/purge_cache")
            {
                Content = JsonContent.Create(new { files = batch }),
            };
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", settings.PurgeToken);

            using var response = await http.SendAsync(request, cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                // The token is never part of the message.
                throw new HttpRequestException($"Cloudflare refused the cache purge with HTTP {(int)response.StatusCode}.", null, response.StatusCode);
            }
        }
    }

    [LoggerMessage(Level = LogLevel.Debug, Message = "Cloudflare purging is not configured; skipped {Count} addresses")]
    private static partial void LogDisabled(ILogger logger, int count);
}
