using System.Net;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace Webling.Cloudflare.Tests;

public sealed class CloudflareCacheTests
{
    private static readonly CloudflareCacheOptions Configured = new()
    {
        ZoneId = "zone/1",
        PurgeToken = "purge-token",
        PublicOrigin = "https://www.example.com/",
    };

    [Fact]
    public async Task Purges_go_out_in_batches_of_at_most_30_addresses()
    {
        var handler = FakeHandler.Json("""{"success":true}""");
        var paths = Enumerable.Range(1, 65).Select(number => $"/page/{number}").ToList();

        await Cache(handler, Configured).PurgeAsync(paths, CancellationToken.None);

        var batches = handler.Requests.Select(request => Files(request.Body)).ToList();
        Assert.Equal([30, 30, 5], batches.Select(batch => batch.Length));
        Assert.Equal(paths.Select(path => "https://www.example.com" + path), batches.SelectMany(batch => batch));
        Assert.All(handler.Requests, request =>
        {
            Assert.Equal(HttpMethod.Post, request.Request.Method);
            Assert.Equal("https://api.cloudflare.com/client/v4/zones/zone%2F1/purge_cache", request.Request.RequestUri!.AbsoluteUri);
            Assert.Equal("Bearer purge-token", request.Request.Headers.Authorization!.ToString());
        });
    }

    [Fact]
    public async Task Each_address_is_purged_once()
    {
        var handler = FakeHandler.Json("""{"success":true}""");

        await Cache(handler, Configured).PurgeAsync(["/a", "/b", "/a"], CancellationToken.None);

        Assert.Equal(["https://www.example.com/a", "https://www.example.com/b"], Files(Assert.Single(handler.Requests).Body));
    }

    [Fact]
    public async Task Nothing_is_sent_until_purging_is_configured()
    {
        var handler = FakeHandler.Json("""{"success":true}""");

        await Cache(handler, new CloudflareCacheOptions { ZoneId = "zone", PublicOrigin = "https://www.example.com" })
            .PurgeAsync(["/a"], CancellationToken.None);

        Assert.Empty(handler.Requests);
    }

    [Fact]
    public async Task A_refused_purge_throws_without_the_token()
    {
        var handler = FakeHandler.Json("""{"success":false}""", HttpStatusCode.Forbidden);

        var exception = await Assert.ThrowsAsync<HttpRequestException>(() => Cache(handler, Configured).PurgeAsync(["/a"], CancellationToken.None));

        Assert.Equal(HttpStatusCode.Forbidden, exception.StatusCode);
        Assert.DoesNotContain("purge-token", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void AddCloudflareCache_points_the_client_at_the_API()
    {
        using var services = new ServiceCollection().AddLogging().AddCloudflareCache().BuildServiceProvider();

        Assert.NotNull(services.GetRequiredService<CloudflareCache>());
    }

    private static string[] Files(string body) =>
        JsonDocument.Parse(body).RootElement.GetProperty("files").EnumerateArray().Select(file => file.GetString()!).ToArray();

    private static CloudflareCache Cache(FakeHandler handler, CloudflareCacheOptions options) =>
        new(new HttpClient(handler) { BaseAddress = CloudflareCache.ApiBase }, Options.Create(options), NullLogger<CloudflareCache>.Instance);
}
