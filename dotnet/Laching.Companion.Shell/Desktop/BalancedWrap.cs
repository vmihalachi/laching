namespace Laching.Companion.Shell.Desktop;

/// <summary>
/// The narrowest whole-pixel width that wraps words into no more lines than the offered width does (Swift's
/// <c>BalancedWrap</c>, CSS's <c>text-wrap: balance</c>), so a bubble's last line doesn't hold a lone word when evener
/// lines fit. <paramref name="height"/> measures the words at a width; a height that never shrinks as the width grows is
/// all it assumes.
/// </summary>
public static class BalancedWrap
{
    public static double Width(double width, Func<double, double> height)
    {
        var lines = height(width);
        var (fits, tooNarrow) = (Math.Ceiling(width), 0.0);
        while (fits - tooNarrow > 1)
        {
            var middle = Math.Floor((fits + tooNarrow) / 2);
            if (height(middle) <= lines)
                fits = middle;
            else
                tooNarrow = middle;
        }
        return Math.Min(width, fits);
    }
}
