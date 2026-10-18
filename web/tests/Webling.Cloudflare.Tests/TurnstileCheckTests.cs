using System.Net;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace Webling.Cloudflare.Tests;

public sealed class TurnstileCheckTests
{
    [Fact]
    public async Task A_passing_token_is_accepted_and_sent_with_the_secret_and_address()
    {
        var handler = FakeHandler.Json("""{"success":true,"error-codes":[]}""");

        Assert.True(await Check(handler).VerifyAsync(new HumanProof("token-1", "203.0.113.9"), CancellationToken.None));

        var (request, body) = Assert.Single(handler.Requests);
        Assert.Equal(HttpMethod.Post, request.Method);
        Assert.Equal(TurnstileCheck.VerifyUrl, request.RequestUri);
        Assert.Equal("secret=s3cret&response=token-1&remoteip=203.0.113.9", body);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    public async Task A_missing_token_is_refused_without_asking(string? token)
    {
        var handler = FakeHandler.Json("""{"success":true}""");

        Assert.False(await Check(handler).VerifyAsync(new HumanProof(token, null), CancellationToken.None));
        Assert.Empty(handler.Requests);
    }

    [Fact]
    public async Task An_overlong_token_is_refused_without_asking()
    {
        var handler = FakeHandler.Json("""{"success":true}""");

        Assert.False(await Check(handler).VerifyAsync(new HumanProof(new string('a', 2049), null), CancellationToken.None));
        Assert.Empty(handler.Requests);
    }

    [Theory]
    [InlineData("""{"success":false,"error-codes":["timeout-or-duplicate"]}""")]
    [InlineData("""{"success":false,"error-codes":["invalid-input-response"]}""")]
    [InlineData("""{"success":false}""")]
    public async Task A_reused_or_failed_token_is_refused(string answer)
    {
        Assert.False(await Check(FakeHandler.Json(answer)).VerifyAsync(new HumanProof("token", null), CancellationToken.None));
    }

    [Fact]
    public async Task A_network_failure_is_refused()
    {
        var check = Check(FakeHandler.Throwing(new HttpRequestException("No route to host")));

        Assert.False(await check.VerifyAsync(new HumanProof("token", null), CancellationToken.None));
    }

    [Fact]
    public async Task A_timeout_is_refused()
    {
        var check = Check(FakeHandler.Throwing(new TaskCanceledException("The request timed out.")));

        Assert.False(await check.VerifyAsync(new HumanProof("token", null), CancellationToken.None));
    }

    [Fact]
    public async Task A_server_error_is_refused()
    {
        var check = Check(FakeHandler.Json("""{"success":true}""", HttpStatusCode.InternalServerError));

        Assert.False(await check.VerifyAsync(new HumanProof("token", null), CancellationToken.None));
    }

    [Fact]
    public async Task An_unreadable_answer_is_refused()
    {
        var check = Check(FakeHandler.Json("<html>Bad gateway</html>"));

        Assert.False(await check.VerifyAsync(new HumanProof("token", null), CancellationToken.None));
    }

    [Fact]
    public void AddTurnstile_registers_the_check_and_refuses_missing_keys()
    {
        using var configured = new ServiceCollection()
            .AddLogging()
            .AddTurnstile()
            .Configure(options =>
            {
                options.SiteKey = TurnstileOptions.TestSiteKey;
                options.SecretKey = TurnstileOptions.TestSecretKey;
            })
            .Services.BuildServiceProvider();
        Assert.IsType<TurnstileCheck>(configured.GetRequiredService<IHumanCheck>());

        using var missing = new ServiceCollection().AddLogging().AddTurnstile().Services.BuildServiceProvider();
        Assert.Throws<OptionsValidationException>(() => missing.GetRequiredService<IOptions<TurnstileOptions>>().Value);
    }

    private static TurnstileCheck Check(FakeHandler handler) =>
        new(new HttpClient(handler), Options.Create(new TurnstileOptions { SiteKey = "site", SecretKey = "s3cret" }), NullLogger<TurnstileCheck>.Instance);
}
