using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Services.Store;
using Windows.System;

namespace Laching.Windows.Shell;

/// <summary>The words of <see cref="AppFeedback.ContactAsync"/>'s fallback dialog, already localized by the app.</summary>
/// <param name="Title">The dialog's title ("Contact us").</param>
/// <param name="CopyAddress">The button that copies the address.</param>
/// <param name="Cancel">The button that closes it.</param>
public sealed record ContactCopy(string Title, string CopyAddress, string Cancel);

/// <summary>
/// Where an app's feedback goes: the maker's email address and the Microsoft Store's review page. Opens them, copies
/// the address and makes the Store's in-app review request; never sends anything itself, and every failure is quiet.
/// <see cref="Laching.Core.Store.ReviewLifecycle"/> decides when to ask. Mirrors Swift's <c>AppFeedback</c>
/// (LachingShell).
/// </summary>
/// <param name="Email">The maker's address.</param>
/// <param name="StoreProductId">The app's Store ID in Partner Center (like <c>9NT3SV8SQ3KD</c>).</param>
public sealed record AppFeedback(string Email, string StoreProductId)
{
    /// <summary>The Store app's write-a-review page.</summary>
    public Uri ReviewUri => new($"ms-windows-store://review/?ProductId={StoreProductId}");

    /// <summary>The web listing, for when the Store app is missing.</summary>
    public Uri ListingUri => new($"https://apps.microsoft.com/detail/{StoreProductId}");

    public Uri MailUri => new("mailto:" + Email);

    /// <summary>Opens the Store on the app's review page, or the web listing when the Store is unavailable.</summary>
    public async Task OpenReviewAsync()
    {
        try
        {
            if (await Launcher.LaunchUriAsync(ReviewUri)) return;
        }
        catch { /* Try the listing when the Store is unavailable. */ }
        try
        {
            await Launcher.LaunchUriAsync(ListingUri);
        }
        catch { /* A missing browser never interrupts the app. */ }
    }

    /// <summary>
    /// The Store's own rate-and-review dialog, parented to <paramref name="window"/>. Call it only after
    /// <see cref="Laching.Core.Store.ReviewLifecycle.BeginAttempt"/> returned true; the Store may still show nothing.
    /// </summary>
    public static async Task RequestReviewAsync(nint window)
    {
        try
        {
            var context = StoreContext.GetDefault();
            WinRT.Interop.InitializeWithWindow.Initialize(context, window);
            await context.RequestRateAndReviewAppAsync();
        }
        catch { /* A Store failure leaves the app as it was. */ }
    }

    /// <summary>Writes an email to <see cref="Email"/>; when no mail app takes it, a dialog over <paramref name="owner"/> offers to copy the address.</summary>
    public async Task ContactAsync(FrameworkElement owner, ContactCopy copy)
    {
        try
        {
            if (await Launcher.LaunchUriAsync(MailUri)) return;
        }
        catch { /* Fall back to the dialog. */ }
        var dialog = new ContentDialog
        {
            XamlRoot = owner.XamlRoot,
            RequestedTheme = owner.ActualTheme,
            Title = copy.Title,
            Content = Email,
            PrimaryButtonText = copy.CopyAddress,
            CloseButtonText = copy.Cancel,
        };
        try
        {
            if (await dialog.ShowAsync() == ContentDialogResult.Primary)
            {
                var data = new DataPackage();
                data.SetText(Email);
                Clipboard.SetContent(data);
            }
        }
        catch { /* The owner may have closed while the mail handler was being resolved. */ }
    }
}
