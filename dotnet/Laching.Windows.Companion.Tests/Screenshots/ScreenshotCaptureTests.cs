using Laching.Windows.Companion.Screenshots;

namespace Laching.Windows.Companion.Tests.Screenshots;

public sealed class ScreenshotCaptureTests
{
    [Theory]
    [InlineData("home", "all", true)]
    [InlineData("home", "", true)]
    [InlineData("support-thanks", "support", true)]
    [InlineData("support-thanks", "home, support-thanks", true)]
    [InlineData("settings", "home,support", false)]
    [InlineData("panel", "Panel", false)]
    public void AShotIsWantedByAPrefixOfItsName(string shot, string mode, bool wanted) =>
        Assert.Equal(wanted, ScreenshotCapture.Wants(shot, mode));

    [Theory]
    [InlineData(null, "light,dark")]
    [InlineData("", "light,dark")]
    [InlineData("dark", "dark")]
    [InlineData("dark, light", "dark,light")]
    [InlineData("contrast", "light,dark")]
    public void TheThemesAskedForAreShotInOrderAndBothWhenUnset(string? raw, string themes) =>
        Assert.Equal(themes, string.Join(",", ScreenshotCapture.ThemesFrom(raw)));

    [Theory]
    [InlineData(null, "")]
    [InlineData("it", "it")]
    [InlineData("it, de,,IT", "it,de")]
    public void TheLanguagesAskedForAreShotInOrderEachOnce(string? raw, string languages) =>
        Assert.Equal(languages, string.Join(",", ScreenshotCapture.LanguagesFrom(raw)));

    [Fact]
    public void ARunOfSeveralLanguagesShootsEachIntoAFolderOfItsOwn()
    {
        string[] names =
        [
            ScreenshotCapture.ModeVariable,
            ScreenshotCapture.DirectoryVariable,
            ScreenshotCapture.LanguageVariable,
            ScreenshotCapture.LanguagesVariable,
        ];
        var before = names.ToDictionary(name => name, Environment.GetEnvironmentVariable);
        try
        {
            Environment.SetEnvironmentVariable(ScreenshotCapture.ModeVariable, "all");
            Environment.SetEnvironmentVariable(ScreenshotCapture.DirectoryVariable, @"C:\shots");
            // The scripts set the one to the first of the several, for an app on an older Laching.
            Environment.SetEnvironmentVariable(ScreenshotCapture.LanguageVariable, "it");
            Environment.SetEnvironmentVariable(ScreenshotCapture.LanguagesVariable, "it,de");
            Assert.Equal(["it", "de"], ScreenshotCapture.Languages);
            Assert.Equal("it", ScreenshotCapture.Language);
            Assert.Equal(@"C:\shots\it", ScreenshotCapture.OutputDirectory);

            Environment.SetEnvironmentVariable(ScreenshotCapture.LanguagesVariable, null);
            Assert.Equal(["it"], ScreenshotCapture.Languages);
            Assert.Equal(@"C:\shots", ScreenshotCapture.OutputDirectory);
        }
        finally
        {
            foreach (var (name, value) in before)
                Environment.SetEnvironmentVariable(name, value);
        }
    }

    [Theory]
    [InlineData("1", true)]
    [InlineData("0", false)]
    [InlineData("", false)]
    [InlineData(null, false)]
    public void ARunIsHiddenOnlyWhenTheScriptsSayOne(string? value, bool hidden)
    {
        var before = Environment.GetEnvironmentVariable(ScreenshotCapture.HiddenVariable);
        try
        {
            Environment.SetEnvironmentVariable(ScreenshotCapture.HiddenVariable, value);
            Assert.Equal(hidden, ScreenshotCapture.IsHidden);
        }
        finally
        {
            Environment.SetEnvironmentVariable(ScreenshotCapture.HiddenVariable, before);
        }
    }

    private const int White = unchecked((int)0xFFFFFFFF);
    private const int Black = unchecked((int)0xFF000000);
    private const int Paper = unchecked((int)0xFFF3F3F3);

    /// <summary>A 20 by 16 capture in <paramref name="background"/>, with grey caption buttons in its top four rows.</summary>
    private static int[] Window(int background)
    {
        var pixels = Enumerable.Repeat(background, 20 * 16).ToArray();
        for (var y = 0; y < 4; y++)
            for (var x = 14; x < 20; x++)
                pixels[y * 20 + x] = unchecked((int)0xFF808080);
        return pixels;
    }

    [Theory]
    [InlineData(White)]
    [InlineData(Black)]
    public void AWindowCapturedBeforeItDrewIsBlank(int background) =>
        Assert.True(ScreenshotCapture.IsBlank(Window(background), width: 20, band: 4, inset: 2));

    [Fact]
    public void AnythingDrawnUnderTheCaptionButtonsIsNotBlank()
    {
        var pixels = Window(Paper);
        pixels[10 * 20 + 9] = Black;
        Assert.False(ScreenshotCapture.IsBlank(pixels, width: 20, band: 4, inset: 2));
    }

    [Fact]
    public void TheFramesEdgesAndAlphaDontCount()
    {
        var pixels = Window(White);
        // The border and a rounded corner, which Windows draws before the content.
        for (var x = 0; x < 20; x++)
            pixels[15 * 20 + x] = Paper;
        pixels[14 * 20 + 0] = 0;
        pixels[10 * 20 + 19] = Black;
        // The same white, see-through.
        pixels[8 * 20 + 8] = 0x00FFFFFF;
        Assert.True(ScreenshotCapture.IsBlank(pixels, width: 20, band: 4, inset: 2));
    }

    [Theory]
    [InlineData(12, 2)]
    [InlineData(4, 10)]
    public void ACaptureWithNothingLeftToLookAtIsNotBlank(int band, int inset) =>
        Assert.False(ScreenshotCapture.IsBlank(Window(White), width: 20, band, inset));

    [Fact]
    public void EachProcessShootsIntoAFolderOfItsOwnUnlessTold()
    {
        var before = Environment.GetEnvironmentVariable(ScreenshotCapture.DirectoryVariable);
        try
        {
            Environment.SetEnvironmentVariable(ScreenshotCapture.DirectoryVariable, null);
            Assert.Equal(Path.Combine(Path.GetTempPath(), $"screenshots-{Environment.ProcessId}"), ScreenshotCapture.OutputDirectory);
            Environment.SetEnvironmentVariable(ScreenshotCapture.DirectoryVariable, @"C:\shots");
            Assert.Equal(@"C:\shots", ScreenshotCapture.OutputDirectory);
        }
        finally
        {
            Environment.SetEnvironmentVariable(ScreenshotCapture.DirectoryVariable, before);
        }
    }
}
