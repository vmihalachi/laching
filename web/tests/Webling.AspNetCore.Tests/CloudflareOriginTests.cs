using System.Net;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Options;

namespace Webling.AspNetCore.Tests;

public sealed class CloudflareOriginTests
{
    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("0123456789abcdef0123456789abcdef-secreT")]
    [InlineData("0123456789abcdef0123456789abcdef-secret ")]
    public async Task Refuses_requests_without_the_exact_secret(string? secret)
    {
        await using var app = await StartAsync();
        using var request = new HttpRequestMessage(HttpMethod.Get, "/");
        if (secret is not null)
        {
            request.Headers.Add("X-Origin-Secret", secret);
        }

        using var response = await app.GetTestClient().SendAsync(request);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Fact]
    public async Task Refuses_the_secret_sent_twice()
    {
        await using var app = await StartAsync();
        using var request = new HttpRequestMessage(HttpMethod.Get, "/");
        request.Headers.Add("X-Origin-Secret", [TestApp.Secret, TestApp.Secret]);

        using var response = await app.GetTestClient().SendAsync(request);

        Assert.Equal(HttpStatusCode.Forbidden, response.StatusCode);
    }

    [Fact]
    public async Task Passes_the_secret_and_hides_it_from_the_app()
    {
        await using var app = await StartAsync();
        using var request = Through(HttpMethod.Get, "/");

        using var response = await app.GetTestClient().SendAsync(request);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("secret header: False", await response.Content.ReadAsStringAsync());
    }

    [Fact]
    public async Task Applies_CF_Connecting_IP_only_after_the_secret_check()
    {
        await using var app = await StartAsync();
        var client = app.GetTestClient();

        using var through = Through(HttpMethod.Get, "/ip");
        through.Headers.Add("CF-Connecting-IP", "203.0.113.9");
        using var accepted = await client.SendAsync(through);
        Assert.Equal("203.0.113.9", await accepted.Content.ReadAsStringAsync());

        using var direct = new HttpRequestMessage(HttpMethod.Get, "/ip");
        direct.Headers.Add("CF-Connecting-IP", "203.0.113.9");
        using var refused = await client.SendAsync(direct);
        Assert.Equal(HttpStatusCode.Forbidden, refused.StatusCode);
        Assert.Empty(await refused.Content.ReadAsStringAsync());
    }

    [Fact]
    public async Task Health_endpoints_answer_only_requests_without_CF_Ray()
    {
        await using var app = await StartAsync();
        var client = app.GetTestClient();

        foreach (var path in new[] { "/health", "/alive" })
        {
            using var probe = await client.GetAsync(path);
            Assert.Equal(HttpStatusCode.OK, probe.StatusCode);

            using var viaCloudflare = Through(HttpMethod.Get, path);
            viaCloudflare.Headers.Add("CF-Ray", "8d3c1f2a3b4c5d6e-AMS");
            using var hidden = await client.SendAsync(viaCloudflare);
            Assert.Equal(HttpStatusCode.NotFound, hidden.StatusCode);
        }
    }

    [Theory]
    [InlineData(null)]
    [InlineData("too-short-for-a-secret")]
    public async Task Startup_fails_outside_Development_without_a_32_character_secret(string? secret)
    {
        await using var app = TestApp.WithCloudflare(secret);
        app.MapGet("/", () => "ok");

        await Assert.ThrowsAsync<OptionsValidationException>(() => app.StartAsync());
    }

    [Fact]
    public async Task Development_runs_without_a_secret_and_checks_nothing()
    {
        await using var app = TestApp.WithCloudflare(secret: null, environment: "Development");
        app.UseCloudflareOrigin();
        app.MapGet("/", () => "ok");
        await app.StartAsync();

        using var response = await app.GetTestClient().GetAsync("/");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    private static async Task<WebApplication> StartAsync()
    {
        var app = TestApp.WithCloudflare();
        app.UseCloudflareOrigin();
        app.MapGet("/", (HttpContext context) => $"secret header: {context.Request.Headers.ContainsKey("X-Origin-Secret")}");
        app.MapGet("/ip", (HttpContext context) => context.Connection.RemoteIpAddress?.ToString() ?? "none");
        app.MapGet("/health", () => "healthy");
        app.MapGet("/alive", () => "alive");
        await app.StartAsync();
        return app;
    }

    private static HttpRequestMessage Through(HttpMethod method, string path)
    {
        var request = new HttpRequestMessage(method, path);
        request.Headers.Add("X-Origin-Secret", TestApp.Secret);
        return request;
    }
}
