using Laching.Companion.Shell.Desktop;
using Microsoft.UI.Xaml.Controls;
using XamlRect = Windows.Foundation.Rect;
using XamlSize = Windows.Foundation.Size;

namespace Laching.Windows.Companion.Views;

/// <summary>
/// Lays its one child (a wrapping <c>TextBlock</c>) out at <see cref="BalancedWrap"/>'s width (Swift's
/// <c>BalancedLines</c>): as many lines as at the width offered, evened out, at the leading edge.
/// </summary>
internal sealed partial class BalancedLines : Panel
{
    private double width;

    protected override XamlSize MeasureOverride(XamlSize available)
    {
        if (Children.Count == 0)
            return default;
        var text = Children[0];
        double Height(double at)
        {
            text.Measure(new XamlSize(at, double.PositiveInfinity));
            return text.DesiredSize.Height;
        }
        Height(available.Width);
        var natural = text.DesiredSize;
        // Lines counted at the width offered, not at the words' own width: a hair under the offer, the TextBlock can
        // wrap once more.
        width = double.IsInfinity(available.Width) ? natural.Width : BalancedWrap.Width(available.Width, Height);
        Height(width);
        return new XamlSize(width, natural.Height);
    }

    protected override XamlSize ArrangeOverride(XamlSize final)
    {
        if (Children.Count > 0)
            Children[0].Arrange(new XamlRect(0, 0, Math.Min(width, final.Width), final.Height));
        return final;
    }
}
