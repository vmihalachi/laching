using System.Numerics;
using Laching.Core.Store;
using Laching.Windows.Companion.Shell;
using Laching.Windows.Companion.Views;
using Laching.Windows.Shell;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Hosting;
using Microsoft.UI.Xaml.Input;
using VirtualKey = Windows.System.VirtualKey;

namespace Laching.Windows.Companion.Support;

/// <summary>
/// The Support window's content (Swift's <c>SupportWindow</c>): first the one-time supporter purchase (shared with the
/// family), then, once that's bought, the tips with the store's prices. One ask at a time, and never right after a
/// purchase: buying the supporter or a tip leaves only a thank-you until the window opens again (the app makes a fresh
/// one for each open, and the model acknowledges the tip when it closes). Restore brings the supporter back. The app
/// opens it only when the user asks; every word and the look are the app's. Esc is Done. Right after a purchase or a
/// tip, focus moves to Done and Narrator hears the thanks.
/// </summary>
public sealed partial class SupportWindow : UserControl
{
    private readonly PurchaseStore purchases;
    private readonly SupportProducts products;
    private readonly SupportCopy copy;
    private readonly SupportLook look;
    private readonly Action onDone;
    private readonly StackPanel body = new() { Spacing = Space.Section };
    private readonly Button restore = new();
    private readonly Button done = HighlightFill.Plain(new Button());
    private readonly Action changed;
    private bool wasSupporter;
    private bool justSupported;
    private SupportStage? shown;
    private object? built;

    public SupportWindow(PurchaseStore purchases, SupportProducts products, SupportCopy copy, SupportLook look, Action onDone)
    {
        this.purchases = purchases;
        this.products = products;
        this.copy = copy;
        this.look = look;
        this.onDone = onDone;
        changed = () =>
        {
            // A rebuild drops the focused ask, and while the ask waits on the store the window's first focus lands on
            // Restore: give it back to the ask, unless someone put it on Restore or Done.
            var refocus = IsLoaded && (restore.FocusState == FocusState.Programmatic || HasFocusWithin(body));
            Rebuild();
            if (refocus)
                FocusAsk();
        };
        IsTabStop = false;
        body.Padding = WindowInset.ContentOverActionBar;
        var scroll = new ScrollViewer { Content = body };
        restore.Content = copy.Restore;
        restore.Style = look.Outline;
        restore.Click += async (_, _) => await purchases.RestoreAsync();
        done.Content = copy.Done;
        done.Style = look.Primary;
        done.Click += (_, _) => onDone();
        var buttons = new Grid();
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        buttons.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        buttons.Children.Add(restore);
        Grid.SetColumn(done, 2);
        buttons.Children.Add(done);
        var bar = new ActionBar();
        bar.Children.Add(buttons);
        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.Children.Add(scroll);
        Grid.SetRow(bar, 1);
        root.Children.Add(bar);
        Content = root;
        var escape = new KeyboardAccelerator { Key = VirtualKey.Escape };
        escape.Invoked += (_, e) =>
        {
            e.Handled = true;
            onDone();
        };
        KeyboardAccelerators.Add(escape);
        // Esc is a convenience, not something to show as a tooltip floating above the window.
        KeyboardAcceleratorPlacementMode = KeyboardAcceleratorPlacementMode.Hidden;
        wasSupporter = purchases.IsOwned(products.Supporter);
        Loaded += async (_, _) =>
        {
            purchases.Changed += changed;
            // WinUI has already focused the ask: rebuild only for a change since, or the focus falls to Restore.
            if (!Equals(built, Built()))
            {
                Rebuild();
                FocusAsk();
            }
            await purchases.LoadProductsIfNeededAsync();
        };
        Unloaded += (_, _) => purchases.Changed -= changed;
        Rebuild();
    }

    /// <summary>What the window shows: a purchase thanks first, then a tip; otherwise the ask that fits.</summary>
    public static SupportStage Stage(bool justSupported, bool justTipped, bool isSupporter) =>
        justSupported ? SupportStage.Welcome
        : justTipped ? SupportStage.Tipped
        : isSupporter ? SupportStage.Supporter
        : SupportStage.Pitch;

    private void Rebuild()
    {
        var isSupporter = purchases.IsOwned(products.Supporter);
        if (isSupporter && !wasSupporter)
            justSupported = true;
        wasSupporter = isSupporter;
        var stage = Stage(justSupported, purchases.JustTipped, isSupporter);
        body.Children.Clear();
        body.Children.Add(look.Title());
        switch (stage)
        {
            case SupportStage.Welcome:
                body.Children.Add(Celebration(copy.WelcomeLine, copy.WelcomeNote, animate: shown is not null && shown != stage));
                break;
            case SupportStage.Tipped:
                body.Children.Add(Celebration(copy.TipThanksLine, null, animate: shown is not null && shown != stage));
                break;
            case SupportStage.Supporter:
                body.Children.Add(Aside(copy.SupporterThanks));
                body.Children.Add(Tips());
                break;
            default:
                body.Children.Add(Aside(copy.Pitch));
                body.Children.Add(Supporter());
                break;
        }
        if (Status() is { } status)
            body.Children.Add(status);
        restore.IsEnabled = !purchases.IsBusy;
        var thanks = Thanks(shown, stage, copy);
        shown = stage;
        built = Built();
        if (thanks is not null && IsLoaded)
        {
            // The bought button is gone: once WinUI has moved focus off it, focus lands on Done on purpose and Narrator
            // hears the thanks after it.
            DispatcherQueue.TryEnqueue(() =>
            {
                done.Focus(FocusState.Programmatic);
                Announcer.Say(done, thanks, Announcer.Thanks);
            });
        }
    }

    /// <summary>What the body was built from, to tell whether the store changed since.</summary>
    private object Built() =>
        (
            purchases.IsOwned(products.Supporter),
            purchases.JustTipped,
            purchases.IsBusy,
            purchases.IsPending,
            purchases.Error,
            purchases.LoadState,
            purchases.Products
        );

    /// <summary>Whether the focused element is <paramref name="root"/> or inside it.</summary>
    private bool HasFocusWithin(DependencyObject root)
    {
        var element = FocusManager.GetFocusedElement(XamlRoot) as DependencyObject;
        while (element is not null && element != root)
            element = Microsoft.UI.Xaml.Media.VisualTreeHelper.GetParent(element);
        return element is not null;
    }

    /// <summary>The focus on the stage's first button (the ask), as WinUI puts it when the window opens.</summary>
    private void FocusAsk() =>
        XamlCast.As<Control>(FocusManager.FindFirstFocusableElement(body))?.Focus(FocusState.Programmatic);

    /// <summary>
    /// The thanks Narrator hears when the window turns from <paramref name="shown"/> to <paramref name="stage"/>: the
    /// welcome line and its note after the supporter purchase, the tip's thanks after a tip, in the app's words; null
    /// for any other stage, or when the stage didn't change.
    /// </summary>
    public static string? Thanks(SupportStage? shown, SupportStage stage, SupportCopy copy) =>
        stage == shown ? null
        : stage == SupportStage.Welcome ? string.IsNullOrEmpty(copy.WelcomeNote) ? copy.WelcomeLine : $"{copy.WelcomeLine} {copy.WelcomeNote}"
        : stage == SupportStage.Tipped ? copy.TipThanksLine
        : null;

    /// <summary>The character beside <paramref name="text"/>.</summary>
    private Grid Aside(string text)
    {
        var row = new Grid { ColumnSpacing = Space.Group };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var character = look.Character(false);
        if (character is FrameworkElement element)
            element.VerticalAlignment = VerticalAlignment.Top;
        row.Children.Add(character);
        var words = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, Space.Item, 0, 0) };
        Grid.SetColumn(words, 1);
        row.Children.Add(words);
        return row;
    }

    /// <summary>The character, large and centered, saying <paramref name="line"/>: the whole window after a purchase, so it reads as thanks, not a shop.</summary>
    private StackPanel Celebration(string line, string? note, bool animate)
    {
        var stack = new StackPanel { Spacing = Space.Group, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, Space.Group, 0, 0) };
        var bubble = look.Bubble(line);
        if (bubble is FrameworkElement b)
            b.HorizontalAlignment = HorizontalAlignment.Center;
        stack.Children.Add(bubble);
        var character = look.Character(true);
        if (character is FrameworkElement c)
            c.HorizontalAlignment = HorizontalAlignment.Center;
        stack.Children.Add(character);
        if (note is not null)
            stack.Children.Add(Soft(note, center: true));
        if (animate && !ReduceMotion.IsOn)
            stack.Loaded += (_, _) => Spring(stack);
        return stack;
    }

    private StackPanel Supporter()
    {
        var stack = new StackPanel { Spacing = Space.Item };
        var price = purchases.Price(products.Supporter);
        var buy = HighlightFill.Plain(
            new Button { Content = copy.BecomeSupporter(price), Style = look.LargePrimary, IsEnabled = !purchases.IsBusy && price is not null }
        );
        buy.Click += async (_, _) => await purchases.BuyAsync(products.Supporter);
        stack.Children.Add(buy);
        stack.Children.Add(Soft(copy.SupporterNote));
        return stack;
    }

    private StackPanel Tips()
    {
        var stack = new StackPanel { Spacing = Space.Item };
        stack.Children.Add(look.TipsHeader());
        var row = new Grid { ColumnSpacing = Space.Item };
        for (var i = 0; i < products.Tips.Count; i++)
        {
            var tip = products.Tips[i];
            row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            var price = purchases.Price(tip);
            var label = new StackPanel { Spacing = Space.Tight, HorizontalAlignment = HorizontalAlignment.Center };
            label.Children.Add(new TextBlock { Text = copy.TipTitle(tip), TextWrapping = TextWrapping.Wrap, TextAlignment = TextAlignment.Center });
            label.Children.Add(look.TipPrice?.Invoke(price ?? "…") ?? new TextBlock { Text = price ?? "…", FontSize = 12, Opacity = 0.8, HorizontalAlignment = HorizontalAlignment.Center });
            // Every card as tall as the tallest, whichever titles wrap: the row's cells stretch.
            var button = new Button
            {
                Content = label,
                Style = look.LargePrimary,
                HorizontalAlignment = HorizontalAlignment.Stretch,
                VerticalAlignment = VerticalAlignment.Stretch,
                IsEnabled = !purchases.IsBusy && price is not null,
            };
            HighlightFill.Plain(button);
            // A button of two texts has no name of its own for Narrator.
            AutomationProperties.SetName(button, price is null ? copy.TipTitle(tip) : $"{copy.TipTitle(tip)}, {price}");
            button.Click += async (_, _) => await purchases.TipAsync(tip);
            Grid.SetColumn(button, i);
            row.Children.Add(button);
        }
        stack.Children.Add(row);
        stack.Children.Add(Soft(copy.TipsNote));
        return stack;
    }

    private FrameworkElement? Status()
    {
        if (purchases.Error is { } error)
        {
            var text = new TextBlock { Text = copy.Error(error), TextWrapping = TextWrapping.Wrap };
            LookBinding.Keep(text, () => text.Foreground = LookBinding.Brush(text, p => p.AccentText));
            return text;
        }
        if (purchases.IsPending)
            return Soft(copy.AskToBuy);
        if (purchases.LoadState != LoadState.Failed)
            return null;
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = Space.Item };
        var failed = Soft(copy.PricesFailed);
        failed.VerticalAlignment = VerticalAlignment.Center;
        row.Children.Add(failed);
        var retry = new Button { Content = copy.TryAgain, Style = look.Outline };
        retry.Click += async (_, _) => await purchases.LoadProductsAsync();
        row.Children.Add(retry);
        return row;
    }

    private static TextBlock Soft(string text, bool center = false)
    {
        var block = new TextBlock
        {
            Text = text,
            FontSize = 12,
            TextWrapping = TextWrapping.Wrap,
            TextAlignment = center ? TextAlignment.Center : TextAlignment.Left,
        };
        LookBinding.Keep(block, () => block.Foreground = LookBinding.Brush(block, p => p.InkSoft));
        return block;
    }

    /// <summary>The celebration springs in: from 85 % and transparent, with a little overshoot (the Mac's spring).</summary>
    private static void Spring(FrameworkElement element)
    {
        var visual = ElementCompositionPreview.GetElementVisual(element);
        var compositor = visual.Compositor;
        visual.CenterPoint = new Vector3((float)element.ActualWidth / 2, (float)element.ActualHeight / 2, 0);
        var overshoot = compositor.CreateCubicBezierEasingFunction(new Vector2(0.34f, 1.4f), new Vector2(0.64f, 1));
        var scale = compositor.CreateVector3KeyFrameAnimation();
        scale.InsertKeyFrame(0, new Vector3(0.85f, 0.85f, 1));
        scale.InsertKeyFrame(1, Vector3.One, overshoot);
        scale.Duration = TimeSpan.FromSeconds(0.45);
        var fade = compositor.CreateScalarKeyFrameAnimation();
        fade.InsertKeyFrame(0, 0);
        fade.InsertKeyFrame(1, 1);
        fade.Duration = TimeSpan.FromSeconds(0.3);
        visual.StartAnimation(nameof(visual.Scale), scale);
        visual.StartAnimation(nameof(visual.Opacity), fade);
    }
}
