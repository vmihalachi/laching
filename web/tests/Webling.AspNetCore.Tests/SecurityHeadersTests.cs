using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;

namespace Webling.AspNetCore.Tests;

public sealed class SecurityHeadersTests
{
    [Fact]
    public async Task The_nonce_the_page_reads_reaches_the_policy()
    {
        await using var app = TestApp.Create("Production");
        app.UseSecurityHeaders(nonce => $"script-src 'nonce-{nonce}'");
        app.MapGet("/", (HttpContext context) => context.GetCspNonce());
        await app.StartAsync();
        var client = app.GetTestClient();

        using var first = await client.GetAsync("/");
        using var second = await client.GetAsync("/");
        var nonce = await first.Content.ReadAsStringAsync();

        Assert.Equal($"script-src 'nonce-{nonce}'", first.Headers.GetValues("Content-Security-Policy").Single());
        Assert.Equal(16, Convert.FromBase64String(nonce).Length);
        Assert.NotEqual(nonce, await second.Content.ReadAsStringAsync());
        Assert.False(first.Headers.Contains("Content-Security-Policy-Report-Only"));
    }

    [Fact]
    public async Task Report_only_sends_the_policy_in_the_report_only_header()
    {
        await using var app = TestApp.Create("Production");
        app.UseSecurityHeaders(nonce => $"script-src 'nonce-{nonce}'", reportOnly: true);
        app.MapGet("/", () => "ok");
        await app.StartAsync();

        using var response = await app.GetTestClient().GetAsync("/");

        Assert.StartsWith("script-src 'nonce-", response.Headers.GetValues("Content-Security-Policy-Report-Only").Single(), StringComparison.Ordinal);
        Assert.False(response.Headers.Contains("Content-Security-Policy"));
    }

    [Fact]
    public async Task Sends_the_fixed_headers_and_HSTS_only_over_HTTPS()
    {
        await using var app = TestApp.Create("Production");
        app.UseSecurityHeaders(_ => "default-src 'none'");
        app.MapGet("/", () => "ok");
        await app.StartAsync();
        var client = app.GetTestClient();

        using var http = await client.GetAsync("http://localhost/");
        Assert.Equal("nosniff", http.Headers.GetValues("X-Content-Type-Options").Single());
        Assert.Equal("DENY", http.Headers.GetValues("X-Frame-Options").Single());
        Assert.Equal("strict-origin-when-cross-origin", http.Headers.GetValues("Referrer-Policy").Single());
        Assert.Contains("camera=()", http.Headers.GetValues("Permissions-Policy").Single(), StringComparison.Ordinal);
        Assert.False(http.Headers.Contains("Strict-Transport-Security"));

        using var https = await client.GetAsync("https://localhost/");
        Assert.Equal("max-age=31536000", https.Headers.GetValues("Strict-Transport-Security").Single());
    }

    [Fact]
    public async Task Reading_the_nonce_without_the_middleware_throws()
    {
        await using var app = TestApp.Create("Production");
        app.MapGet("/", (HttpContext context) => context.GetCspNonce());
        await app.StartAsync();

        await Assert.ThrowsAsync<InvalidOperationException>(() => app.GetTestClient().GetAsync("/"));
    }
}
