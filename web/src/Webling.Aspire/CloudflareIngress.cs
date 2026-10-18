using System.Net;
using System.Net.Sockets;
using Azure.Provisioning.AppContainers;
using Azure.Provisioning.Expressions;

namespace Webling.Aspire;

/// <summary>
/// Puts a public Container App behind Cloudflare: ingress accepts only Cloudflare's IPv4 ranges, probes carry the
/// public host so they pass <c>AllowedHosts</c>, and the host is bound with a Cloudflare Origin CA certificate
/// uploaded to the Container Apps environment.
/// </summary>
public static class CloudflareIngress
{
    /// <summary>
    /// Where Cloudflare publishes its IPv4 ranges.
    /// </summary>
    public static readonly Uri Ipv4RangesUrl = new("https://www.cloudflare.com/ips-v4");

    private static readonly Lazy<IReadOnlyList<string>> Bundled = new(() =>
    {
        using var stream = typeof(CloudflareIngress).Assembly.GetManifestResourceStream("Webling.Aspire.cloudflare-ips-v4.txt")
            ?? throw new InvalidOperationException("The bundled Cloudflare range list is missing.");
        using var reader = new StreamReader(stream);
        return ParseRanges(reader.ReadToEnd(), "the bundled list");
    });

    /// <summary>
    /// Cloudflare's IPv4 ranges as of this package version. An origin without an AAAA record is always reached
    /// over IPv4.
    /// </summary>
    public static IReadOnlyList<string> Ipv4Ranges => Bundled.Value;

    /// <summary>
    /// Reads a copy of <see cref="Ipv4RangesUrl"/>, one CIDR per line, for an app that keeps its own list.
    /// </summary>
    public static IReadOnlyList<string> LoadRanges(string path) => ParseRanges(File.ReadAllText(path), path);

    /// <summary>
    /// Parses one IPv4 CIDR range per line, ignoring blank lines and surrounding whitespace. Throws when a line is
    /// not an IPv4 range or there are none. <paramref name="source"/> names the list in the error.
    /// </summary>
    public static IReadOnlyList<string> ParseRanges(string text, string source)
    {
        ArgumentNullException.ThrowIfNull(text);

        var ranges = text.Split('\n')
            .Select(line => line.Trim())
            .Where(line => line.Length > 0)
            .ToList();

        foreach (var range in ranges)
        {
            if (!range.Contains('/', StringComparison.Ordinal)
                || !IPNetwork.TryParse(range, out var network)
                || network.BaseAddress.AddressFamily != AddressFamily.InterNetwork)
            {
                throw new InvalidOperationException($"'{range}' in {source} is not an IPv4 CIDR range.");
            }
        }

        if (ranges.Count == 0)
        {
            throw new InvalidOperationException($"{source} lists no Cloudflare IP ranges.");
        }

        return ranges;
    }

    /// <summary>
    /// Fetches <see cref="Ipv4RangesUrl"/> and throws when it differs from <paramref name="ranges"/> (by default
    /// <see cref="Ipv4Ranges"/>). Call it when publishing, so a deployment fails instead of locking Cloudflare out
    /// of the origin.
    /// </summary>
    public static async Task EnsureRangesCurrentAsync(IReadOnlyList<string>? ranges = null, HttpClient? http = null, CancellationToken cancellationToken = default)
    {
        ranges ??= Ipv4Ranges;
        using var owned = http is null ? new HttpClient() : null;
        var published = ParseRanges(await (http ?? owned!).GetStringAsync(Ipv4RangesUrl, cancellationToken), Ipv4RangesUrl.ToString());

        var added = published.Except(ranges, StringComparer.Ordinal).ToList();
        var removed = ranges.Except(published, StringComparer.Ordinal).ToList();
        if (added.Count > 0 || removed.Count > 0)
        {
            throw new InvalidOperationException(
                $"Cloudflare's IPv4 ranges changed (added: {string.Join(", ", added)}; removed: {string.Join(", ", removed)}). " +
                "Update the list before deploying.");
        }
    }

    /// <summary>
    /// Allows only <paramref name="ranges"/> (by default <see cref="Ipv4Ranges"/>) through the app's ingress, sends
    /// <paramref name="publicHostname"/> as the Host of every probe, and, given <paramref name="certificateName"/>,
    /// binds the host with that certificate from the environment's <c>certificates/</c>. Call it from
    /// <c>PublishAsAzureContainerApp</c>.
    /// </summary>
    public static ContainerApp UseCloudflareIngress(this ContainerApp app, string publicHostname, string? certificateName = null, IReadOnlyList<string>? ranges = null)
    {
        ArgumentNullException.ThrowIfNull(app);
        ArgumentException.ThrowIfNullOrEmpty(publicHostname);

        ranges ??= Ipv4Ranges;
        var ingress = app.Configuration.Ingress;

        // Once any Allow rule exists, Container Apps denies every other address.
        for (var i = 0; i < ranges.Count; i++)
        {
            ingress.IPSecurityRestrictions.Add(new ContainerAppIPSecurityRestrictionRule
            {
                Name = $"cloudflare-{i}",
                IPAddressRange = ranges[i],
                Action = ContainerAppIPRuleAction.Allow,
                Description = "Cloudflare",
            });
        }

        // Probes reach the container directly with the pod IP as Host, which AllowedHosts would reject.
        foreach (var container in app.Template.Containers)
        {
            foreach (var probe in container.Value!.Probes)
            {
                probe.Value!.HttpGet.HttpHeaders.Add(new ContainerAppHttpHeaderInfo
                {
                    Name = "Host",
                    Value = publicHostname,
                });
            }
        }

        if (certificateName is null)
        {
            return app;
        }

        // An uploaded Cloudflare Origin CA certificate lives under certificates/, not managedCertificates/,
        // so Aspire's ConfigureCustomDomain does not apply.
        ingress.CustomDomains.Add(new ContainerAppCustomDomain
        {
            Name = publicHostname,
            BindingType = ContainerAppCustomDomainBindingType.SniEnabled,
            CertificateId = new InterpolatedStringExpression(
            [
                app.EnvironmentId.Compile(),
                new StringLiteralExpression($"/certificates/{certificateName}"),
            ]),
        });

        return app;
    }
}
