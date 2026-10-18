using Laching.Core.Settings;
using Laching.Companion.Cadence;
using Laching.Companion.Settings;

namespace Laching.Companion.Tests;

public sealed class ChatterCadenceTests
{
    [Fact]
    public void Presets()
    {
        int[][] minutes = [[15, 5], [30, 10], [45, 10], [60, 15], [90, 20], [120, 30], [180, 45]];
        Assert.Equal(minutes, Frequencies.All.Select(f => new[] { (int)f.IntervalSeconds() / 60, (int)f.JitterSeconds() / 60 }));
        Assert.Equal(
            ["chatty", "sometimes", "every45Minutes", "rarely", "every90Minutes", "every2Hours", "every3Hours"],
            Frequencies.All.Select(f => f.RawValue()));
    }

    [Fact]
    public void ConfigMapsSettingsOntoTheScheduler()
    {
        var config = ChatterCadence.Config(Frequency.Chatty, 8 * 60, 20 * 60, new HashSet<int> { 1, 7 }, new HashSet<int> { 13 }, false);
        Assert.Equal(900.0, config.IntervalSeconds);
        Assert.Equal(300.0, config.IntervalJitterSeconds);
        Assert.Equal(480, config.ActiveStartMinute);
        Assert.Equal(1200, config.ActiveEndMinute);
        Assert.Equal(new[] { 1, 7 }, config.ActiveWeekdays.Order().ToArray());
        Assert.Equal(new[] { 13 }, config.QuietHours.Order().ToArray());
        Assert.Equal(300.0, config.IdleThresholdSeconds);
        Assert.True(config.PostponeDuringCalls);
        Assert.False(config.PostponeDuringFullScreen);
        Assert.Equal(30.0, config.BusyGraceSeconds);
    }

    [Fact]
    public void DefaultSettingsGiveTheDefaultConfig()
    {
        var settings = new CompanionSettings(new InMemorySettingsStore(), Fixture.Defaults);
        var config = ChatterCadence.Config(settings);
        Assert.Equal(3600.0, config.IntervalSeconds);
        Assert.Equal(900.0, config.IntervalJitterSeconds);
        Assert.Equal(new[] { 2, 3, 4, 5, 6 }, config.ActiveWeekdays.Order().ToArray());
        Assert.True(config.PostponeDuringFullScreen);
    }

    [Fact]
    public void RawValuesRoundTrip()
    {
        foreach (var frequency in Frequencies.All)
            Assert.Equal(frequency, Frequencies.Parse(frequency.RawValue()));
        foreach (var shush in Shushes.All)
            Assert.Equal(shush, Shushes.Parse(shush.RawValue()));
        Assert.Null(Frequencies.Parse("Sometimes"));
        Assert.Null(Shushes.Parse("forever"));
    }

    [Fact]
    public void AnUnknownPresetOrShushThrows()
    {
        var unknown = (Frequency)99;
        Assert.Throws<ArgumentOutOfRangeException>(() => unknown.IntervalSeconds());
        Assert.Throws<ArgumentOutOfRangeException>(() => unknown.JitterSeconds());
        Assert.Throws<ArgumentOutOfRangeException>(() => unknown.RawValue());
        Assert.Throws<ArgumentOutOfRangeException>(() => ((ChatterCadence.Shush)99).RawValue());
    }
}
