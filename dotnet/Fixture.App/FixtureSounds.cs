namespace FixtureApp;

/// <summary>
/// The fixture's sounds, written as tones at launch (the fixture bank names a tune; the model plays a blip, a hush and a
/// hello): distinct enough to tell apart by ear, and nothing to commit.
/// </summary>
public static class FixtureSounds
{
    public const string Blip = "blip";

    public const string Shh = "shh";

    public const string Hello = "hello";

    private const int Rate = 22050;

    /// <summary>Writes every sound into a folder of the fixture's own under the temp folder; returns its path.</summary>
    public static string Write()
    {
        var folder = Path.Combine(Path.GetTempPath(), "Laching fixture", "Sounds");
        Directory.CreateDirectory(folder);
        WriteTones(folder, Blip, [(880, 0.08)]);
        WriteTones(folder, Shh, [(523, 0.12), (392, 0.18)]);
        WriteTones(folder, Hello, [(523, 0.1), (659, 0.1), (784, 0.16)]);
        WriteTones(folder, "tune-1", [(392, 0.18), (440, 0.18), (494, 0.18), (523, 0.18), (587, 0.18), (523, 0.3)]);
        return folder;
    }

    /// <summary>A mono 16-bit WAV of sine notes (frequency in Hz, length in seconds), each faded in and out.</summary>
    private static void WriteTones(string folder, string name, (double Hz, double Seconds)[] notes)
    {
        var samples = new List<short>();
        foreach (var (hz, seconds) in notes)
        {
            var count = (int)(Rate * seconds);
            var fade = Math.Min(count / 4, Rate / 100);
            for (var i = 0; i < count; i++)
            {
                var envelope = Math.Min(1.0, Math.Min(i, count - 1 - i) / (double)Math.Max(1, fade));
                samples.Add((short)(Math.Sin(2 * Math.PI * hz * i / Rate) * envelope * 0.3 * short.MaxValue));
            }
        }
        var path = Path.Combine(folder, name + ".wav");
        using var writer = new BinaryWriter(File.Create(path));
        writer.Write("RIFF"u8);
        writer.Write(36 + samples.Count * 2);
        writer.Write("WAVEfmt "u8);
        writer.Write(16);
        writer.Write((short)1); // PCM
        writer.Write((short)1); // mono
        writer.Write(Rate);
        writer.Write(Rate * 2);
        writer.Write((short)2);
        writer.Write((short)16);
        writer.Write("data"u8);
        writer.Write(samples.Count * 2);
        foreach (var sample in samples)
            writer.Write(sample);
    }
}
