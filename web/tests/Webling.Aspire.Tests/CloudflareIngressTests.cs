using System.Net;
using Azure.Provisioning.AppContainers;

namespace Webling.Aspire.Tests;

public sealed class CloudflareIngressTests
{
    [Fact]
    public void Parses_one_range_per_line_ignoring_blank_lines_and_whitespace()
    {
        var ranges = CloudflareIngress.ParseRanges("173.245.48.0/20\r\n\n  103.21.244.0/22  \n", "test");

        Assert.Equal(["173.245.48.0/20", "103.21.244.0/22"], ranges);
    }

    [Theory]
    [InlineData("2400:cb00::/32")]
    [InlineData("173.245.48.0")]
    [InlineData("173.245.48.0/33")]
    [InlineData("not a range")]
    public void Refuses_anything_but_IPv4_CIDR_ranges(string line)
    {
        var exception = Assert.Throws<InvalidOperationException>(() => CloudflareIngress.ParseRanges("103.21.244.0/22\n" + line, "test.txt"));

        Assert.Contains($"'{line}' in test.txt", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Refuses_an_empty_list()
    {
        Assert.Throws<InvalidOperationException>(() => CloudflareIngress.ParseRanges("\n \n", "test"));
    }

    [Fact]
    public void The_bundled_list_is_the_committed_file()
    {
        var file = Path.Combine(AppContext.BaseDirectory, "cloudflare-ips-v4.txt");

        Assert.Equal(CloudflareIngress.LoadRanges(file), CloudflareIngress.Ipv4Ranges);
        Assert.Contains("173.245.48.0/20", CloudflareIngress.Ipv4Ranges);
    }

    [Fact]
    public async Task EnsureRangesCurrent_throws_when_Cloudflare_publishes_a_different_list()
    {
        using var same = new HttpClient(new Answer(string.Join('\n', CloudflareIngress.Ipv4Ranges)));
        await CloudflareIngress.EnsureRangesCurrentAsync(http: same);

        using var changed = new HttpClient(new Answer(string.Join('\n', CloudflareIngress.Ipv4Ranges.Skip(1).Append("198.51.100.0/24"))));
        var exception = await Assert.ThrowsAsync<InvalidOperationException>(() => CloudflareIngress.EnsureRangesCurrentAsync(http: changed));
        Assert.Contains("added: 198.51.100.0/24", exception.Message, StringComparison.Ordinal);
        Assert.Contains("removed: " + CloudflareIngress.Ipv4Ranges[0], exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void UseCloudflareIngress_allows_only_the_ranges_and_sends_the_host_on_probes()
    {
        var app = new ContainerApp("web");
        app.Configuration.Ingress = new ContainerAppIngressConfiguration();
        app.Template = new ContainerAppTemplate();
        var container = new ContainerAppContainer { Name = "web" };
        container.Probes.Add(new ContainerAppProbe { HttpGet = new ContainerAppHttpRequestInfo { Path = "/health", Port = 8080 } });
        app.Template.Containers.Add(container);

        app.UseCloudflareIngress("www.example.com", ranges: ["173.245.48.0/20", "103.21.244.0/22"]);

        var rules = app.Configuration.Ingress.IPSecurityRestrictions.Select(rule => rule.Value!).ToList();
        Assert.Equal(["173.245.48.0/20", "103.21.244.0/22"], rules.Select(rule => rule.IPAddressRange.Value));
        Assert.All(rules, rule => Assert.Equal(ContainerAppIPRuleAction.Allow, rule.Action.Value));
        var header = Assert.Single(container.Probes.Single().Value!.HttpGet.HttpHeaders).Value!;
        Assert.Equal("Host", header.Name.Value);
        Assert.Equal("www.example.com", header.Value.Value);
        Assert.Empty(app.Configuration.Ingress.CustomDomains);
    }

    private sealed class Answer(string body) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Assert.Equal(CloudflareIngress.Ipv4RangesUrl, request.RequestUri);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(body) });
        }
    }
}
