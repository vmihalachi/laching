using Laching.Companion.Shell.Desktop;

namespace Laching.Companion.Shell.Tests.Desktop;

/// <summary>Ported from the Swift <c>BalancedWrapTests</c>.</summary>
public sealed class BalancedWrapTests
{
    /// <summary>Greedy wrapping of words in a monospaced face, 10 pixels a character, 20 a line.</summary>
    private static Func<double, double> Height(string text)
    {
        var words = text.Split(' ').Select(word => word.Length * 10.0).ToArray();
        return width =>
        {
            var (lines, line) = (1.0, 0.0);
            foreach (var word in words)
            {
                if (line > 0 && line + 10 + word > width)
                    (lines, line) = (lines + 1, word);
                else
                    line += line > 0 ? 10 + word : word;
            }
            return lines * 20;
        };
    }

    [Fact]
    public void TwoLinesMeetInTheMiddle()
    {
        // At 270: "one two three four five six" over "seven". Balanced: "one two three four" over "five six seven".
        var measure = Height("one two three four five six seven");
        Assert.Equal(180, BalancedWrap.Width(270, measure));
        Assert.Equal(measure(270), measure(180));
        Assert.True(measure(179) > measure(270));
    }

    [Fact]
    public void EvenLinesStayAsWide() =>
        // At 150: "Present! Sunny," / "reporting for" / "duty."; any narrower takes a fourth line.
        Assert.Equal(150, BalancedWrap.Width(150, Height("Present! Sunny, reporting for duty.")));

    [Fact]
    public void OneLineHugsItsWords() => Assert.Equal(120, BalancedWrap.Width(200, Height("Hello there!")));
}
