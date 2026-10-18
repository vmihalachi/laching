using Laching.Companion.Figures;
using Laching.Companion.Rules;

namespace Laching.Companion.Tests;

/// <summary>The rig over the vector fixture (<c>Tests/Fixtures/characters/fixture-vector</c>).</summary>
public sealed class RiggedCharacterTests
{
    private static readonly RiggedCharacter Character = new(Fixture.Vector);

    [Fact]
    public void EveryMoodPicksItsParts()
    {
        Assert.Equal("eyes-open", Character.StillPose(Mood.Awake).Eyes);
        Assert.Equal("mouth-smile", Character.StillPose(Mood.Awake).Mouth);
        Assert.Equal("mouth-flat", Character.StillPose(Mood.Shushed).Mouth);
        var asleep = Character.StillPose(Mood.Asleep);
        Assert.Equal("eyes-closed", asleep.Eyes);
        Assert.Equal(["zz"], asleep.Overlays);
        Assert.Equal(["cheeks"], asleep.HeadExtras);
        Assert.Empty(Character.StillPose(Mood.OffHours).HeadExtras);
        Assert.Equal(["cheeks"], Character.StillPose(Mood.Awake).HeadExtras);
        Assert.Empty(Character.StillPose(Mood.Awake).Overlays);
        Assert.Equal("mouth-flat", Character.StillPose(Mood.HushedForCall).Mouth);
    }

    [Theory]
    [InlineData(0, "mouth-flat")]
    [InlineData(1, "mouth-talk-1")]
    [InlineData(2, "mouth-talk-2")]
    [InlineData(3, "mouth-talk-2")]
    public void MouthLevelsFollowTheVoice(int level, string mouth)
    {
        // The fixture talks with mouth-talk-1 and mouth-talk-2 and rests on mouth-flat between words.
        var pose = Character.Pose(Mood.Talking, 0.05, level);
        Assert.Equal(mouth, pose.Mouth);
        Assert.Equal(0.03 * level / 3, pose.Pulse, 12);
    }

    [Fact]
    public void MouthLevelsClampAndNilKeepsTheTimer()
    {
        Assert.Equal("mouth-talk-2", Character.Pose(Mood.Talking, 0.2, 9).Mouth);
        Assert.Equal("mouth-flat", Character.Pose(Mood.Talking, 0.2, -1).Mouth);
        Assert.Equal(Character.Pose(Mood.Talking, 0.2), Character.Pose(Mood.Talking, 0.2, null));
        Assert.Equal(Character.Pose(Mood.Talking, 0.2).HeadAngle, Character.Pose(Mood.Talking, 0.2, 2).HeadAngle);
    }

    [Fact]
    public void VoicedMouthIndexSpreadsTheLevels()
    {
        Assert.Equal([0, 0, 0], new[] { 1, 2, 3 }.Select(l => RiggedCharacter.VoicedMouthIndex(l, 1)));
        Assert.Equal([0, 1, 1], new[] { 1, 2, 3 }.Select(l => RiggedCharacter.VoicedMouthIndex(l, 2)));
        Assert.Equal([0, 1, 2], new[] { 1, 2, 3 }.Select(l => RiggedCharacter.VoicedMouthIndex(l, 3)));
        Assert.Equal([0, 1, 3], new[] { 1, 2, 3 }.Select(l => RiggedCharacter.VoicedMouthIndex(l, 4)));
    }

    [Theory]
    [InlineData(Mood.Shushed, 14, 2)]
    [InlineData(Mood.Asleep, 24, 4)]
    [InlineData(Mood.OffHours, 10, 0)]
    [InlineData(Mood.Awake, 0, 0)]
    public void DroopAndSink(Mood mood, double angle, double sink)
    {
        var pose = Character.Pose(mood, 0);
        Assert.Equal(angle, pose.HeadAngle, 9);
        Assert.Equal(sink, pose.Sink, 9);
    }

    [Fact]
    public void BlinkSchedule()
    {
        // Intervals 4.2, 5.6, 3.8 (a 13.6 s cycle); each blink is the interval's last 0.12 s.
        Assert.True(Character.Pose(Mood.Awake, 4.15).Blinking);
        Assert.Equal("eyes-closed", Character.Pose(Mood.Awake, 4.15).Eyes);
        Assert.False(Character.Pose(Mood.Awake, 4.0).Blinking);
        Assert.False(Character.Pose(Mood.Awake, 4.2).Blinking);
        Assert.True(Character.Pose(Mood.Awake, 13.6 + 4.15).Blinking);
        for (var time = 0.0; time < 40; time += 0.01)
            Assert.False(Character.Pose(Mood.Shushed, time).Blinking);
    }

    [Fact]
    public void LargeTimesStayExact()
    {
        Assert.True(Character.Pose(Mood.Awake, 13.6 * 40_000 + 4.15).Blinking);
        Assert.Equal(Character.Pose(Mood.Awake, 1).HeadAngle, Character.Pose(Mood.Awake, 4 * 1_000_000 + 1).HeadAngle, 9);
    }

    [Fact]
    public void StillPoseNeverBlinksOrTalks()
    {
        foreach (var mood in Moods.All)
        {
            var pose = Character.StillPose(mood);
            Assert.False(pose.Blinking);
            Assert.Equal(Character.Art.Rig.Moods[mood].Mouth[0], pose.Mouth);
        }
    }

    [Fact]
    public void TalkingAlternatesMouths()
    {
        Assert.Equal("mouth-talk-1", Character.Pose(Mood.Talking, 0.05).Mouth);
        Assert.Equal("mouth-talk-2", Character.Pose(Mood.Talking, 0.2).Mouth);
    }

    [Fact]
    public void BlendSwitchesPartsAtHalf()
    {
        var from = Character.StillPose(Mood.Shushed);
        var to = Character.StillPose(Mood.Awake);
        var quarter = from.Blended(to, 0.25);
        Assert.Equal(["cheeks"], quarter.HeadExtras);
        Assert.Equal(11.5, quarter.HeadAngle, 9);
        Assert.Equal("mouth-smile", from.Blended(to, 0.5).Mouth);
        Assert.Equal("mouth-flat", from.Blended(to, 0.25).Mouth);
        Assert.Equal(to, from.Blended(to, 1));
    }

    [Fact]
    public void BlendEndsExactlyOnTheTarget()
    {
        var from = Character.StillPose(Mood.Shushed);
        var to = Character.Pose(Mood.Awake, 2); // sin(π): a headAngle of about 5e-16
        Assert.Equal(to, from.Blended(to, 1));
        Assert.Equal(from, from.Blended(to, 0));
    }

    [Fact]
    public void ShapesStayInsideTheViewBox()
    {
        var box = Character.ViewBox;
        foreach (var mood in Moods.All)
        {
            foreach (var time in new[] { 0, 1, 4.15, 0.2 })
            {
                foreach (var point in Character.Shapes(Character.Pose(mood, time)).SelectMany(s => s.Path).SelectMany(c => c.Points))
                {
                    Assert.InRange(point.X, -2, box.Width + 2);
                    Assert.InRange(point.Y, -2, box.Height + 2);
                }
            }
        }
    }

    [Fact]
    public void ShapesCarryTheMoodsParts()
    {
        Assert.Contains(Character.Shapes(Character.StillPose(Mood.Awake)), s => s.Fill == "blush");
        Assert.DoesNotContain(Character.Shapes(Character.StillPose(Mood.OffHours)), s => s.Fill == "blush");
        Assert.Contains(Character.Shapes(Character.StillPose(Mood.Asleep)), s => s.Stroke == "inkSoft");
    }

    [Fact]
    public void LayersFollowTheDrawingOrder()
    {
        Assert.Equal(
            ["stem", "head", "face", "cheeks", "eyes-closed", "mouth-flat", "zz"],
            Character.Layers(Character.StillPose(Mood.Asleep)).Select(l => l.Name)
        );
        var layers = Character.Layers(Character.Pose(Mood.Asleep, 0));
        Assert.Equal(Transform.Identity, layers[^1].Transform); // overlays are never moved
        Assert.Equal(layers[3].Transform, layers[4].Transform); // head extras ride on the head
    }

    [Fact]
    public void ShapesAreTheLayersFlattened()
    {
        var pose = Character.Pose(Mood.Talking, 0.2);
        var flattened = Character
            .Layers(pose)
            .SelectMany(l => l.Content is PartContent.Vector v ? v.Shapes.Select(s => s.Transformed(l.Transform)) : [])
            .ToArray();
        Assert.Equal(flattened, Character.Shapes(pose));
    }

    [Fact]
    public void DroopRotatesTheHeadAroundTheNeck()
    {
        var upright = Character.Shapes(Character.Pose(Mood.Awake, 0));
        var drooped = Character.Shapes(Character.Pose(Mood.OffHours, 0));
        static Point Center(IReadOnlyList<PathShape> shapes, PaintRole fill)
        {
            var points = shapes.First(s => s.Fill == fill).Path.SelectMany(c => c.Points).ToArray();
            return new Point(points.Average(p => p.X), points.Average(p => p.Y));
        }
        // The face moves right and down (clockwise in y-down coordinates); the stem follows a quarter as far.
        Assert.True(Center(drooped, "edge").X > Center(upright, "edge").X + 3);
        Assert.True(Center(drooped, "edge").Y > Center(upright, "edge").Y);
        var stemShift = Center(drooped, "stem").X - Center(upright, "stem").X;
        Assert.True(Math.Abs(stemShift) < Center(drooped, "edge").X - Center(upright, "edge").X);
    }

    [Fact]
    public void GlyphIsTemplateInk()
    {
        foreach (var mood in Moods.All)
        {
            for (var frame = 0; frame < Character.GlyphFrameCount(mood); frame++)
            {
                var shapes = Character.Glyph(mood, frame);
                Assert.NotEmpty(shapes);
                foreach (var shape in shapes)
                    Assert.True(shape.Fill is null || shape.Fill == PaintRole.Ink && (shape.Stroke is null || shape.Stroke == PaintRole.Ink));
            }
        }
        Assert.Equal(2, Character.GlyphFrameCount(Mood.Talking));
        Assert.NotEqual(Character.Glyph(Mood.Talking, 0), Character.Glyph(Mood.Talking, 1));
        Assert.Equal(Character.Glyph(Mood.Talking, 0), Character.Glyph(Mood.Talking, 2));
        Assert.Equal(18, Character.GlyphSize);
    }

    [Fact]
    public void BubblePointIsTheBubblePivot()
    {
        Assert.Equal(new BubbleAnchor("face", 0.1), Fixture.Vector.Rig.Bubble);
        Assert.Equal(new Point(50, 40), Fixture.Vector.BubblePoint);
    }

    [Fact]
    public void TransformsComposeInOrder()
    {
        var p = Transform.Rotation(90, new Point(0, 0)).Then(Transform.Translation(10, 0)).Apply(new Point(1, 0));
        Assert.Equal(10, p.X, 12);
        Assert.Equal(1, p.Y, 12);
        Assert.Equal(new Point(3, 1), Transform.Scale(2, new Point(1, 1)).Apply(new Point(2, 1)));
        Assert.Equal((1d, 0d, 0d, 1d, 3d, 4d), Transform.Translation(3, 4).Affine);
    }

    [Fact]
    public void ArtMissingWhatItsRigNamesThrows()
    {
        // The messages are Swift's preconditionFailure messages (docs/conventions.md).
        var art = Fixture.Vector;
        var noRig = new RiggedCharacter(art with { Rig = art.Rig with { Moods = new Dictionary<Mood, MoodRig>() } });
        var error = Assert.Throws<InvalidOperationException>(() => noRig.StillPose(Mood.Awake));
        Assert.Equal($"{art.Skin.RawValue} has no rig for awake", error.Message);
        var pose = Character.StillPose(Mood.Awake);
        var noPivots = new RiggedCharacter(art with { Pivots = new Dictionary<string, Point>() });
        error = Assert.Throws<InvalidOperationException>(() => noPivots.Layers(pose));
        Assert.Equal($"{art.Skin.RawValue} has no pivot {art.Rig.Head.Pivot}", error.Message);
        var noParts = new RiggedCharacter(art with { Parts = new Dictionary<string, PartContent>() });
        error = Assert.Throws<InvalidOperationException>(() => noParts.Layers(pose));
        Assert.StartsWith($"{art.Skin.RawValue} has no part ", error.Message);
    }
}
