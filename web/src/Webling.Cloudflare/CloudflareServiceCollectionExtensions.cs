using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace Webling.Cloudflare;

public static class CloudflareServiceCollectionExtensions
{
    /// <summary>
    /// Registers <see cref="TurnstileCheck"/> as the <see cref="IHumanCheck"/>, with a typed HttpClient, and
    /// <see cref="TurnstileOptions"/> bound from <paramref name="configuration"/> (the <c>Turnstile</c> section). The
    /// app does not start without both keys; chain <c>PostConfigure</c> on the result to fill in
    /// <see cref="TurnstileOptions.TestSiteKey"/> and <see cref="TurnstileOptions.TestSecretKey"/> in development.
    /// </summary>
    public static OptionsBuilder<TurnstileOptions> AddTurnstile(this IServiceCollection services, IConfiguration? configuration = null)
    {
        ArgumentNullException.ThrowIfNull(services);

        services.AddHttpClient<IHumanCheck, TurnstileCheck>();

        var options = services.AddOptions<TurnstileOptions>();
        if (configuration is not null)
        {
            options.Bind(configuration);
        }

        return options
            .Validate(turnstile => turnstile.SiteKey.Length > 0 && turnstile.SecretKey.Length > 0, "Set Turnstile:SiteKey and Turnstile:SecretKey.")
            .ValidateOnStart();
    }

    /// <summary>
    /// Registers <see cref="CloudflareCache"/> with a typed HttpClient for Cloudflare's API, and
    /// <see cref="CloudflareCacheOptions"/> bound from <paramref name="configuration"/> (the <c>Cloudflare</c>
    /// section). Purging stays off until the zone, token and public origin are all set.
    /// </summary>
    public static IServiceCollection AddCloudflareCache(this IServiceCollection services, IConfiguration? configuration = null)
    {
        ArgumentNullException.ThrowIfNull(services);

        var options = services.AddOptions<CloudflareCacheOptions>();
        if (configuration is not null)
        {
            options.Bind(configuration);
        }

        services.AddHttpClient<CloudflareCache>(client => client.BaseAddress = CloudflareCache.ApiBase);
        return services;
    }
}
