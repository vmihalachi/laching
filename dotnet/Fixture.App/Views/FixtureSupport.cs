using Laching.Companion.Rules;
using Laching.Testing.Fixtures;
using Laching.Windows.Companion.Support;
using Laching.Windows.Companion.Views;
using Laching.Windows.Shell;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;

namespace FixtureApp.Views;

/// <summary>The Support window over the mock store: every word plain English, the fixture's character and a bubble.</summary>
public static class FixtureSupport
{
    public const string Tip = "fixture.tip";

    public static SupportWindow Make(FixtureModel model) =>
        new(
            model.Purchases,
            new SupportProducts(FixtureModel.Supporter, new[] { Tip }),
            new SupportCopy(
                Pitch: "The fixture is made by one person. Supporting it keeps it going, and says thanks.",
                BecomeSupporter: price => price is null ? "Become a supporter" : $"Become a supporter, {price}",
                SupporterNote: "Pay once. Shared with your family.",
                SupporterThanks: "Thank you for supporting the fixture. A tip now and then is welcome too.",
                TipTitle: _ => "A small tip",
                TipsNote: "Tips unlock nothing: they're thanks.",
                WelcomeLine: "Thank you!",
                WelcomeNote: "You're a supporter now.",
                TipThanksLine: "Thanks for the tip!",
                Error: error => $"That didn't go through ({error}).",
                AskToBuy: "Waiting for approval.",
                PricesFailed: "The prices didn't load.",
                TryAgain: "Try again",
                Restore: "Restore purchases",
                Done: "Done"
            ),
            new SupportLook(
                Title: () => Heading("Support", "TitleTextBlockStyle", AutomationHeadingLevel.Level1),
                TipsHeader: () => Heading("Leave a tip", "BodyStrongTextBlockStyle", AutomationHeadingLevel.Level2),
                Character: celebrating => new CharacterCanvas
                {
                    Character = model.GlyphCharacter,
                    Mood = celebrating ? Mood.Talking : Mood.Awake,
                    Width = celebrating ? 150 : 96,
                    Height = celebrating ? 150 : 96,
                    AccessibilityLabel = "The fixture",
                },
                Bubble: line => new SpeechBubble { Text = line, Tail = BubbleTail.Bottom, FontSize = 18, FillsWidth = false },
                Primary: Style("AccentButtonStyle"),
                LargePrimary: Style("AccentButtonStyle")
            ),
            () => model.Windows.Close(FixtureWindow.Support)
        );

    /// <summary>A title that Narrator's heading navigation finds.</summary>
    private static TextBlock Heading(string text, string style, AutomationHeadingLevel level)
    {
        var block = new TextBlock { Text = text, Style = Style(style) };
        AutomationProperties.SetHeadingLevel(block, level);
        return block;
    }

    /// <summary>Through XamlCast: under Native AOT an untyped resource can arrive as a plain DependencyObject.</summary>
    private static Style Style(string key) => XamlCast.As<Style>(Application.Current.Resources[key])!;
}
