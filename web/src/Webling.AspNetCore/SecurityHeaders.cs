using System.Security.Cryptography;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Hosting;

namespace Webling.AspNetCore;

/// <summary>
/// Security headers sent by the app itself, so they hold without the edge: HSTS on HTTPS outside Development,
/// <c>nosniff</c>, <c>DENY</c> framing, a strict referrer policy, a permissions policy that turns off camera,
/// geolocation, microphone, payment and USB, and a content security policy with a fresh nonce per request.
/// </summary>
public static class SecurityHeaders
{
    private static readonly object NonceKey = new();

    /// <summary>
    /// Adds the headers to every response. <paramref name="contentSecurityPolicy"/> receives the per-request nonce
    /// that inline and framework scripts must carry; read it in components with <see cref="GetCspNonce"/>. With
    /// <paramref name="reportOnly"/> the policy goes in <c>Content-Security-Policy-Report-Only</c>.
    /// </summary>
    public static WebApplication UseSecurityHeaders(this WebApplication app, Func<string, string> contentSecurityPolicy, bool reportOnly = false)
    {
        ArgumentNullException.ThrowIfNull(app);
        ArgumentNullException.ThrowIfNull(contentSecurityPolicy);

        var sendHsts = !app.Environment.IsDevelopment();

        app.Use((context, next) =>
        {
            var nonce = Convert.ToBase64String(RandomNumberGenerator.GetBytes(16));
            context.Items[NonceKey] = nonce;

            var headers = context.Response.Headers;
            if (sendHsts && context.Request.IsHttps)
            {
                headers.StrictTransportSecurity = "max-age=31536000";
            }

            headers.XContentTypeOptions = "nosniff";
            headers.XFrameOptions = "DENY";
            headers["Referrer-Policy"] = "strict-origin-when-cross-origin";
            headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(), payment=(), usb=()";
            if (reportOnly)
            {
                headers.ContentSecurityPolicyReportOnly = contentSecurityPolicy(nonce);
            }
            else
            {
                headers.ContentSecurityPolicy = contentSecurityPolicy(nonce);
            }

            return next(context);
        });

        return app;
    }

    /// <summary>
    /// Returns the content security policy nonce for the current request.
    /// </summary>
    public static string GetCspNonce(this HttpContext context)
    {
        ArgumentNullException.ThrowIfNull(context);

        return context.Items[NonceKey] as string
            ?? throw new InvalidOperationException($"Call {nameof(UseSecurityHeaders)} before reading the nonce.");
    }
}
