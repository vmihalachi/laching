using System.Xml.Linq;
using Laching.Windows.Shell;
using Microsoft.Windows.AppLifecycle;

namespace Laching.Windows.Tests.Shell;

/// <summary>
/// How a copy Windows started for a notification click knows it, and what it hands over; the notification's XML.
/// </summary>
public sealed class AppNotifierTests
{
    private const string App = @"C:\Program Files\Example\Example.exe";

    [Fact]
    public void AClickLaunchCarriesTheServersArgument()
    {
        Assert.True(AppNotifier.IsClickLaunch([App, AppNotifier.ClickArgument]));
        Assert.False(AppNotifier.IsClickLaunch([App]));
        Assert.False(AppNotifier.IsClickLaunch([App, "--first-run"]));
    }

    [Fact]
    public void OnlyAClickIsHandedOver() => Assert.True(AppNotifier.IsClick(ExtendedActivationKind.AppNotification));

    [Theory]
    [InlineData(ExtendedActivationKind.Launch)]
    [InlineData(ExtendedActivationKind.StartupTask)]
    [InlineData(ExtendedActivationKind.Protocol)]
    [InlineData(ExtendedActivationKind.File)]
    public void AnyOtherKindIsDropped(ExtendedActivationKind kind) => Assert.False(AppNotifier.IsClick(kind));

    [Fact]
    public void ThePayloadHasTheBuildersShape()
    {
        var payload = AppNotifier.Payload(
            "action",
            "banner",
            "Sunny says",
            "A line.",
            [new("Say more", "SAY_MORE"), new("Shh for an hour", "SHH_HOUR")],
            sound: false
        );

        Assert.Equal(
            "<toast launch=\"action=banner\"><visual><binding template=\"ToastGeneric\"><text>Sunny says</text><text>A line.</text>"
                + "</binding></visual><audio silent=\"true\" /><actions><action content=\"Say more\" arguments=\"action=SAY_MORE\" />"
                + "<action content=\"Shh for an hour\" arguments=\"action=SHH_HOUR\" /></actions></toast>",
            payload
        );
    }

    [Fact]
    public void ApostrophesAmpersandsAndBracketsComeBackAsTheyWere()
    {
        const string Title = "L'amico & co <3";
        const string Body = "Un po' di \"sole\" > niente.";
        const string Button = "Shh per un'ora & più <subito>";

        var toast = XElement.Parse(AppNotifier.Payload("action", "banner", Title, Body, [new(Button, "SHH")], sound: true));

        Assert.Equal([Title, Body], toast.Descendants("text").Select(text => text.Value));
        Assert.Equal(Button, toast.Descendants("action").Single().Attribute("content")!.Value);
    }

    [Fact]
    public void AnEmptyBodyNoButtonsAndSoundLeaveTheirElementsOut()
    {
        var toast = XElement.Parse(AppNotifier.Payload("action", "banner", "Title", "", [], sound: true));

        Assert.Single(toast.Descendants("text"));
        Assert.Empty(toast.Elements("audio"));
        Assert.Empty(toast.Elements("actions"));
    }

    [Fact]
    public void ArgumentsEncodeTheirSeparators() => Assert.Equal("k%3Bey=a%3Bb%3Dc%25d", AppNotifier.Argument("k;ey", "a;b=c%d"));

    [Fact]
    public void SixButtonsAreRefused() =>
        Assert.Throws<ArgumentException>(() =>
            AppNotifier.Payload("action", "banner", "Title", "", [.. Enumerable.Range(0, 6).Select(i => new NotificationButton($"{i}", $"{i}"))], true)
        );
}
