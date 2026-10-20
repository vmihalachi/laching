package laching.android

import android.app.Activity
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.Choreographer
import android.view.PixelCopy
import android.view.View
import android.view.ViewGroup
import android.view.inspector.WindowInspector
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.File
import kotlin.coroutines.resume

/**
 * The Android `ScreenshotCapture`: when the app was launched with [LaunchContext.screens], [run] shows each screen
 * in light then dark (the app's [Screens] does the showing), copies the window through [PixelCopy] into
 * `filesDir/screens/<screen>-<theme>.png` with the app's other windows over it ([overlayWindows]), lists layout
 * problems in `problems.txt` (content taller than the viewport outside a scroll container, found here in the view
 * tree; text with `hasVisualOverflow`, which the app's Compose text reports through [report]), writes `done` and
 * finishes the activity. Laching's `screens.py` launches the app with the extras and pulls the folder.
 */
public class ScreenshotRun(
    private val activity: Activity,
    public val launch: LaunchContext,
) {
    /** The app's side: show a screen in a theme and return once its state is set; the run waits for the frame. */
    public fun interface Screens {
        public suspend fun show(
            screen: String,
            dark: Boolean,
        )
    }

    public val folder: File = File(activity.filesDir, "screens")
    private val problems = mutableListOf<String>()

    /** The shot being captured, `<screen>-<theme>`, for [report]; null outside a run. */
    public var current: String? = null
        private set

    /** The viewport's height in pixels, for the app's own overflow checks. */
    public val viewportHeight: Int
        get() = activity.window.decorView.height

    /** Notes a problem with the current shot, e.g. a Compose text whose layout `hasVisualOverflow`. */
    public fun report(problem: String) {
        problems += "${current ?: "-"}: $problem"
    }

    /** Captures every screen, writes `problems.txt` and `done`, then finishes [activity]. */
    public suspend fun run(
        screens: Screens,
        settleMillis: Long = 400,
    ) {
        folder.mkdirs()
        folder.listFiles()?.forEach { it.delete() }
        for (screen in launch.screens) {
            for (dark in listOf(false, true)) {
                val shot = "$screen-${if (dark) "dark" else "light"}"
                current = shot
                screens.show(screen, dark)
                awaitFrame()
                delay(settleMillis)
                awaitFrame()
                capture(File(folder, "$shot.png"))
                checkViewTree()
            }
        }
        current = null
        File(folder, "problems.txt").writeText(problems.joinToString("") { "$it\n" })
        File(folder, "done").writeText("")
        activity.finishAndRemoveTask()
    }

    private suspend fun awaitFrame() {
        suspendCancellableCoroutine { continuation ->
            Choreographer.getInstance().postFrameCallback { continuation.resume(Unit) }
        }
    }

    private suspend fun capture(file: File) {
        val view = activity.window.decorView
        if (view.width == 0 || view.height == 0) {
            report("the window has no size")
            return
        }
        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)
        try {
            val code =
                retryingTimeouts({ it }) {
                    suspendCancellableCoroutine { continuation ->
                        PixelCopy.request(activity.window, bitmap, { continuation.resume(it) }, Handler(Looper.getMainLooper()))
                    }
                }
            if (code != PixelCopy.SUCCESS) {
                report("PixelCopy failed with $code")
                return
            }
            overlayWindows(bitmap, view)
        } catch (error: IllegalArgumentException) {
            // PixelCopy throws for a window with no surface: the activity isn't on a screen that's on, as under the
            // lock screen a foldable shows when it's folded shut.
            report("the window has no surface to copy (${error.message}): is the screen locked or off?")
            return
        }
        file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    /** [copy]'s result, copied again a frame later while it times out, as a busy emulator's copy sometimes does. */
    private suspend fun <T> retryingTimeouts(
        status: (T) -> Int,
        copy: suspend () -> T,
    ): T {
        var result = copy()
        repeat(COPY_RETRIES) {
            if (status(result) != PixelCopy.ERROR_TIMEOUT) return result
            awaitFrame()
            result = copy()
        }
        return result
    }

    /**
     * The app's other windows drawn over the activity's, where the screen shows them: a Compose `ModalBottomSheet`
     * or `Dialog` is a window of its own, which the copy of the activity's window leaves out. A window is copied
     * through [PixelCopy.Request] from Android 14; before it, a shot with a window over the activity is reported.
     */
    private suspend fun overlayWindows(
        bitmap: Bitmap,
        decor: View,
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val windows =
            WindowInspector.getGlobalWindowViews().filter {
                it !== decor && it.isAttachedToWindow && it.isShown && it.width > 0 && it.height > 0
            }
        if (windows.isEmpty()) return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            report("${windows.size} window(s) over the activity are left out: copying one needs Android 14")
            return
        }
        val canvas = Canvas(bitmap)
        val origin = IntArray(2).also(decor::getLocationOnScreen)
        val at = IntArray(2)
        val main = activity.mainExecutor
        for (root in windows) {
            val result =
                retryingTimeouts({ it.status }) {
                    val request =
                        PixelCopy.Request.Builder
                            .ofWindow(root)
                            .build()
                    suspendCancellableCoroutine { continuation ->
                        PixelCopy.request(request, main, continuation::resume)
                    }
                }
            if (result.status != PixelCopy.SUCCESS) {
                report("PixelCopy of a window over the activity failed with ${result.status}")
                continue
            }
            root.getLocationOnScreen(at)
            canvas.drawBitmap(result.bitmap, (at[0] - origin[0]).toFloat(), (at[1] - origin[1]).toFloat(), null)
        }
    }

    /** Classic views whose bottom lies below the viewport with no scrolling ancestor; Compose reports its own. */
    private fun checkViewTree() {
        val root = activity.window.decorView
        val viewport = root.height
        val location = IntArray(2)

        fun walk(
            view: View,
            scrollable: Boolean,
        ) {
            if (view.visibility != View.VISIBLE) return
            val inScroll =
                scrollable || view.isScrollContainer || view.canScrollVertically(1) || view.canScrollVertically(-1)
            if (view is ViewGroup) {
                for (i in 0 until view.childCount) walk(view.getChildAt(i), inScroll)
                return
            }
            view.getLocationInWindow(location)
            val bottom = location[1] + view.height
            if (!inScroll && view.height > 0 && bottom > viewport) {
                report(
                    "${describe(view)} extends ${bottom - viewport} px below the viewport outside a scroll container",
                )
            }
        }
        walk(root, false)
    }

    private fun describe(view: View): String {
        val name = view.javaClass.simpleName
        val id =
            if (view.id !=
                View.NO_ID
            ) {
                runCatching { view.resources.getResourceEntryName(view.id) }.getOrNull()
            } else {
                null
            }
        return if (id != null) "$name #$id" else name
    }

    private companion object {
        /** How many times a copy that timed out is made again. */
        const val COPY_RETRIES = 2
    }
}
