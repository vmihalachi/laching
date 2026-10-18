using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;

namespace Webling.AspNetCore.Tests;

/// <summary>
/// A WebApplication on TestServer.
/// </summary>
internal static class TestApp
{
    public const string Secret = "0123456789abcdef0123456789abcdef-secret";

    public static WebApplication Create(string environment, Action<WebApplicationBuilder>? configure = null)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions { EnvironmentName = environment });
        builder.WebHost.UseTestServer();
        builder.Logging.ClearProviders();
        configure?.Invoke(builder);
        return builder.Build();
    }

    public static WebApplication WithCloudflare(string? secret = Secret, string environment = "Production") =>
        Create(environment, builder =>
        {
            builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?> { ["Cloudflare:OriginSecret"] = secret });
            builder.AddCloudflareOrigin();
        });
}
