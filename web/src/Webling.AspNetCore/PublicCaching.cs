using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Components.Endpoints;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;
using Microsoft.Net.Http.Headers;

namespace Webling.AspNetCore;

/// <summary>
/// Settings for <see cref="PublicCaching"/>.
/// </summary>
public sealed class PublicCachingOptions
{
    /// <summary>
    /// Cookies that change what a public page shows, such as the sign-in cookie (account links in the header) or a
    /// consent cookie (a banner). A request carrying any of them gets a private answer.
    /// </summary>
    public IList<string> PersonalCookies { get; } = new List<string>();
}

/// <summary>
/// Cache headers for public pages and documents. A shared cache such as Cloudflare keeps a public answer for five
/// minutes and serves it stale for ten more while it refreshes. A visitor with a personal cookie sees a different
/// page, so those answers stay private.
/// </summary>
public static class PublicCaching
{
    public const string PageCacheControl = "public, s-maxage=300, stale-while-revalidate=600";

    /// <summary>
    /// Sets the personal cookies, by name.
    /// </summary>
    public static IServiceCollection AddPublicCaching(this IServiceCollection services, params string[] personalCookies)
    {
        ArgumentNullException.ThrowIfNull(services);
        ArgumentNullException.ThrowIfNull(personalCookies);

        services.Configure<PublicCachingOptions>(options =>
        {
            foreach (var cookie in personalCookies)
            {
                options.PersonalCookies.Add(cookie);
            }
        });

        return services;
    }

    /// <summary>
    /// Applies <see cref="PageCacheControl"/> to GET and HEAD requests for components marked
    /// <see cref="PublicPageAttribute"/>, through <see cref="Apply"/>. Call it after authentication.
    /// </summary>
    public static IApplicationBuilder UsePublicPageCaching(this IApplicationBuilder app)
    {
        ArgumentNullException.ThrowIfNull(app);

        return app.Use((context, next) =>
        {
            var component = context.GetEndpoint()?.Metadata.GetMetadata<ComponentTypeMetadata>()?.Type;
            if (component?.IsDefined(typeof(PublicPageAttribute), inherit: true) == true
                && (HttpMethods.IsGet(context.Request.Method) || HttpMethods.IsHead(context.Request.Method)))
            {
                context.Response.OnStarting(() =>
                {
                    if (!IsPersonal(context.Request))
                    {
                        DropAntiforgery(context.Response);
                    }

                    Apply(context, PageCacheControl, varyByCookie: true);
                    return Task.CompletedTask;
                });
            }

            return next(context);
        });
    }

    /// <summary>
    /// Sets <paramref name="cacheControl"/> when the answer is the same for every visitor: a 200 or a 410, no
    /// cookie being set, no personal cookie in the request and nothing already forbidding storage. Otherwise the
    /// answer stays private. <paramref name="varyByCookie"/> marks answers whose content depends on cookies.
    /// </summary>
    public static void Apply(HttpContext context, string cacheControl, bool varyByCookie)
    {
        ArgumentNullException.ThrowIfNull(context);

        var response = context.Response;
        if (varyByCookie)
        {
            response.Headers.Append(HeaderNames.Vary, HeaderNames.Cookie);
        }

        var shared = response.StatusCode is StatusCodes.Status200OK or StatusCodes.Status410Gone
            && response.Headers.SetCookie.Count == 0
            && !response.Headers.CacheControl.ToString().Contains("no-store", StringComparison.OrdinalIgnoreCase)
            && !IsPersonal(context.Request);

        if (shared)
        {
            response.Headers.CacheControl = cacheControl;
        }
        else if (string.IsNullOrEmpty(response.Headers.CacheControl))
        {
            response.Headers.CacheControl = "private, no-cache";
        }
    }

    /// <summary>
    /// Whether the request carries one of the <see cref="PublicCachingOptions.PersonalCookies"/>.
    /// </summary>
    public static bool IsPersonal(HttpRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);

        var options = request.HttpContext.RequestServices.GetService<IOptions<PublicCachingOptions>>()?.Value;
        return options is not null && options.PersonalCookies.Any(request.Cookies.ContainsKey);
    }

    // With interactive render modes enabled, Blazor writes an antiforgery token into the persisted state of every
    // static page, which sets the antiforgery cookie and forbids caching. Public pages post no form that needs the
    // token, so for anonymous visitors the cookie and its no-cache headers are dropped. The token left in the page
    // is useless without its cookie. The pages set no cache headers of their own on GET.
    private static void DropAntiforgery(HttpResponse response)
    {
        var cookies = response.Headers.SetCookie;
        if (cookies.Count > 0)
        {
            response.Headers.SetCookie = cookies.Where(cookie => cookie?.StartsWith(".AspNetCore.Antiforgery.", StringComparison.Ordinal) != true).ToArray();
        }

        // A visitor who already has the cookie gets no new one, but the headers still forbid caching.
        response.Headers.Remove(HeaderNames.Pragma);
        response.Headers.Remove(HeaderNames.CacheControl);
    }
}
