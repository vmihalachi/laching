using Laching.Companion.Figures;
using Laching.Companion.Rules;

namespace Laching.Windows.Companion.Views;

/// <summary>
/// The pose <see cref="CharacterCanvas"/> shows at a moment (Swift's <c>CharacterView</c>'s statics): the mood's pose at
/// the seconds since it appeared, the mouth on a recorded clip's loudness while talking, the still pose when nothing may
/// move, and the spring back up after Shh. Pure, so the tests run without WinUI.
/// </summary>
public static class CharacterMotion
{
    /// <summary>How long the spring back from Shh takes.</summary>
    public const double WakeDuration = 0.6;

    /// <summary>The spring back from Shh: critically damped (no overshoot), 0 at the start, settled at <see cref="WakeDuration"/>.</summary>
    public static double WakeProgress(double elapsed)
    {
        if (!(elapsed > 0))
            return 0;
        if (elapsed >= WakeDuration)
            return 1;
        var omega = 10 / WakeDuration;
        return 1 - (1 + omega * elapsed) * Math.Exp(-omega * elapsed);
    }

    /// <summary>The pose <paramref name="elapsed"/> seconds into waking: the drooped pose blending into the live one.</summary>
    public static Pose Waking(Pose drooped, Pose target, double elapsed) => drooped.Blended(target, WakeProgress(elapsed));

    /// <summary>The mood drawn: talking while the voice plays, else the model's.</summary>
    public static Mood Shown(Mood mood, bool talking) => talking ? Mood.Talking : mood;

    /// <summary>
    /// The pose to draw. <paramref name="still"/> holds the mood's still pose (reduce motion, not animated, a screenshot
    /// run); otherwise the pose at <paramref name="time"/> seconds, the mouth on <paramref name="mouthLevel"/> while
    /// talking (null keeps the mood's timer), blended up from the Shh droop for <see cref="WakeDuration"/> after waking
    /// (<paramref name="sinceWake"/>, null when it didn't just wake).
    /// </summary>
    public static Pose PoseAt(RiggedCharacter character, Mood mood, bool talking, bool still, double time, int? mouthLevel, double? sinceWake)
    {
        var shown = Shown(mood, talking);
        if (still)
            return character.StillPose(shown);
        var pose = character.Pose(shown, time, talking ? mouthLevel : null);
        if (sinceWake is { } elapsed && elapsed < WakeDuration)
            pose = Waking(character.StillPose(Mood.Shushed), pose, elapsed);
        return pose;
    }
}
