using System.Net;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Components.Endpoints;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.DependencyInjection;

namespace Webling.AspNetCore.Tests;

[PublicPage]
public sealed class PublicPage;

public sealed class PrivatePage;

public sealed class PublicCachingTests
{
    private const string Personal = "__Host-session";

    [Fact]
    public async Task An_anonymous_200_is_shared()
    {
        var response = await GetAsync("/document");

        Assert.Equal(PublicCaching.PageCacheControl, response.Headers.CacheControl!.ToString());
        Assert.Contains("Cookie", response.Headers.Vary);
    }

    [Fact]
    public async Task A_410_is_shared_too()
    {
        var response = await GetAsync("/gone");

        Assert.Equal(PublicCaching.PageCacheControl, response.Headers.CacheControl!.ToString());
    }

    [Fact]
    public async Task A_personal_cookie_makes_it_private()
    {
        var response = await GetAsync("/document", cookie: Personal + "=1");

        Assert.True(response.Headers.CacheControl is { Private: true, NoCache: true, Public: false });
    }

    [Fact]
    public async Task Another_cookie_keeps_it_shared()
    {
        var response = await GetAsync("/document", cookie: "theme=dark");

        Assert.Equal(PublicCaching.PageCacheControl, response.Headers.CacheControl!.ToString());
    }

    [Theory]
    [InlineData("/missing")]
    [InlineData("/sets-cookie")]
    [InlineData("/no-store")]
    public async Task Errors_cookies_and_no_store_are_never_shared(string path)
    {
        var response = await GetAsync(path);

        Assert.DoesNotContain("public", response.Headers.CacheControl?.ToString() ?? "", StringComparison.Ordinal);
    }

    [Fact]
    public async Task A_public_page_drops_the_antiforgery_cookie_for_anonymous_visitors()
    {
        var response = await GetAsync("/public-page");

        Assert.Equal(PublicCaching.PageCacheControl, response.Headers.CacheControl!.ToString());
        Assert.Null(response.Headers.Pragma.FirstOrDefault());
        Assert.False(response.Headers.TryGetValues("Set-Cookie", out var cookies) && cookies.Any(cookie => cookie.StartsWith(".AspNetCore.Antiforgery.", StringComparison.Ordinal)));
    }

    [Fact]
    public async Task A_public_page_stays_private_for_a_signed_in_visitor()
    {
        var response = await GetAsync("/public-page", cookie: Personal + "=1");

        // Blazor's own no-store stays, and so does the antiforgery cookie the signed-in visitor needs.
        Assert.True(response.Headers.CacheControl is { NoStore: true, Public: false });
        Assert.Contains(response.Headers.GetValues("Set-Cookie"), cookie => cookie.StartsWith(".AspNetCore.Antiforgery.", StringComparison.Ordinal));
    }

    [Fact]
    public async Task A_page_without_the_attribute_is_left_alone()
    {
        var response = await GetAsync("/private-page");

        Assert.True(response.Headers.CacheControl is { NoStore: true, NoCache: true, Public: false });
    }

    private static async Task<HttpResponseMessage> GetAsync(string path, string? cookie = null)
    {
        var app = TestApp.Create("Production", builder => builder.Services.AddPublicCaching(Personal));
        await using (app)
        {
            app.UsePublicPageCaching();

            app.MapGet("/document", (HttpContext context) => Shared(context, StatusCodes.Status200OK));
            app.MapGet("/gone", (HttpContext context) => Shared(context, StatusCodes.Status410Gone));
            app.MapGet("/missing", (HttpContext context) => Shared(context, StatusCodes.Status404NotFound));
            app.MapGet("/sets-cookie", (HttpContext context) =>
            {
                context.Response.Cookies.Append("visit", "1", new CookieOptions { Secure = true });
                return Shared(context, StatusCodes.Status200OK);
            });
            app.MapGet("/no-store", (HttpContext context) =>
            {
                context.Response.Headers.CacheControl = "no-store";
                return Shared(context, StatusCodes.Status200OK);
            });

            // What Blazor's antiforgery support does to a static page: a cookie and headers that forbid caching.
            app.MapGet("/public-page", Antiforgery).WithMetadata(new ComponentTypeMetadata(typeof(PublicPage)));
            app.MapGet("/private-page", Antiforgery).WithMetadata(new ComponentTypeMetadata(typeof(PrivatePage)));

            await app.StartAsync();
            using var request = new HttpRequestMessage(HttpMethod.Get, path);
            if (cookie is not null)
            {
                request.Headers.Add("Cookie", cookie);
            }

            var response = await app.GetTestClient().SendAsync(request);
            await response.Content.LoadIntoBufferAsync();
            return response;
        }

        static string Shared(HttpContext context, int status)
        {
            context.Response.StatusCode = status;
            PublicCaching.Apply(context, PublicCaching.PageCacheControl, varyByCookie: true);
            return "ok";
        }

        static string Antiforgery(HttpContext context)
        {
            context.Response.Headers.Append("Set-Cookie", ".AspNetCore.Antiforgery.abc=token; path=/; samesite=strict; httponly");
            context.Response.Headers.CacheControl = "no-cache, no-store";
            context.Response.Headers.Pragma = "no-cache";
            return "page";
        }
    }
}
