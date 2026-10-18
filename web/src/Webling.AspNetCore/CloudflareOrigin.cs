using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;

namespace Webling.AspNetCore;

/// <summary>
/// Settings for <see cref="CloudflareOrigin"/>, bound from the <c>Cloudflare</c> configuration section.
/// </summary>
public sealed class CloudflareOriginOptions
{
    public const string SectionName = "Cloudflare";

    /// <summary>
    /// The value a Cloudflare Transform Rule sends in <c>X-Origin-Secret</c>. Required outside Development, at least
    /// 32 characters.
    /// </summary>
    public string? OriginSecret { get; set; }

    /// <summary>
    /// Health endpoints that answer only requests from inside the hosting environment, never through Cloudflare.
    /// </summary>
    public string[] HealthPaths { get; set; } = ["/health", "/alive"];
}

/// <summary>
/// Origin protection behind Cloudflare. An ingress that accepts only Cloudflare's IP ranges still lets any
/// Cloudflare zone through; the shared secret proves the request passed through your zone and its rules.
/// </summary>
public static class CloudflareOrigin
{
    private const string OriginSecretHeader = "X-Origin-Secret";
    private const string RayHeader = "CF-Ray";
    private const string ConnectingIpHeader = "CF-Connecting-IP";

    /// <summary>
    /// Registers <see cref="CloudflareOriginOptions"/>. Outside Development the app does not start without a
    /// secret of at least 32 characters, so it can never serve traffic unprotected.
    /// </summary>
    public static TBuilder AddCloudflareOrigin<TBuilder>(this TBuilder builder) where TBuilder : IHostApplicationBuilder
    {
        ArgumentNullException.ThrowIfNull(builder);

        var isDevelopment = builder.Environment.IsDevelopment();

        builder.Services.AddOptions<CloudflareOriginOptions>()
            .Bind(builder.Configuration.GetSection(CloudflareOriginOptions.SectionName))
            .Validate(
                options => isDevelopment || options.OriginSecret is { Length: >= 32 },
                "Cloudflare:OriginSecret must be set (at least 32 characters) outside Development.")
            .ValidateOnStart();

        return builder;
    }

    /// <summary>
    /// Rejects requests that did not come through the Cloudflare zone with 403, answers 404 to health endpoint
    /// requests that came through Cloudflare, and then applies the client IP from <c>CF-Connecting-IP</c> and the
    /// scheme from <c>X-Forwarded-Proto</c>. Call it before any other middleware. Does nothing when no secret is
    /// configured (Development).
    /// </summary>
    public static WebApplication UseCloudflareOrigin(this WebApplication app)
    {
        ArgumentNullException.ThrowIfNull(app);

        var options = app.Services.GetRequiredService<IOptions<CloudflareOriginOptions>>().Value;
        if (string.IsNullOrEmpty(options.OriginSecret))
        {
            return app;
        }

        var secretBytes = Encoding.UTF8.GetBytes(options.OriginSecret);
        var healthPaths = options.HealthPaths.Select(path => new PathString(path)).ToArray();

        app.Use((context, next) =>
        {
            var request = context.Request;

            if (healthPaths.Any(path => request.Path.StartsWithSegments(path)))
            {
                // Every request through any Cloudflare zone carries CF-Ray; probes and calls from inside the
                // hosting environment do not.
                if (request.Headers.ContainsKey(RayHeader))
                {
                    context.Response.StatusCode = StatusCodes.Status404NotFound;
                    return Task.CompletedTask;
                }

                return next(context);
            }

            var provided = request.Headers[OriginSecretHeader];
            if (provided.Count != 1
                || !CryptographicOperations.FixedTimeEquals(Encoding.UTF8.GetBytes(provided[0] ?? ""), secretBytes))
            {
                context.Response.StatusCode = StatusCodes.Status403Forbidden;
                return Task.CompletedTask;
            }

            // Keep the secret out of anything that logs or forwards request headers.
            request.Headers.Remove(OriginSecretHeader);

            return next(context);
        });

        var forwardedHeaders = new ForwardedHeadersOptions
        {
            ForwardedForHeaderName = ConnectingIpHeader,
            ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto,
        };

        // Only requests that passed the secret check, or health probes from inside the environment, get here,
        // so the client IP (from Cloudflare) and the scheme (from the ingress) are trusted whatever the
        // immediate peer is.
        forwardedHeaders.KnownIPNetworks.Clear();
        forwardedHeaders.KnownProxies.Clear();

        app.UseForwardedHeaders(forwardedHeaders);

        return app;
    }
}
