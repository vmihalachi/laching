package laching.android

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.annotation.DrawableRes
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * The reminder notification: its channel, a builder that takes the app's copy and actions (this package ships no
 * strings), and the plumbing that tells the app when the user swiped it away (`deleteIntent`). The app asks for
 * `POST_NOTIFICATIONS` itself (Android 13+) and checks [canPost] before [show].
 */
public class ReminderNotifications(
    private val context: Context,
    public val channelId: String,
) {
    private val manager = NotificationManagerCompat.from(context)

    /** The texts of one notification, localized by the app. */
    public data class Copy(
        public val title: CharSequence,
        public val text: CharSequence,
        public val subtext: CharSequence? = null,
    )

    /** A button on the notification. */
    public data class Action(
        @param:DrawableRes public val icon: Int,
        public val title: CharSequence,
        public val intent: PendingIntent,
    )

    /** Creates or updates the channel; the user's own changes to it win over [importance]. */
    public fun ensureChannel(
        name: CharSequence,
        description: String? = null,
        importance: Int = NotificationManager.IMPORTANCE_HIGH,
    ) {
        val channel = NotificationChannel(channelId, name, importance)
        channel.description = description
        manager.createNotificationChannel(channel)
    }

    /** Whether the app may post on the channel: permission granted (Android 13+), notifications on, channel not off. */
    public val canPost: Boolean
        get() {
            if (!hasPermission) return false
            if (!manager.areNotificationsEnabled()) return false
            val channel = manager.getNotificationChannel(channelId) ?: return true
            return channel.importance != NotificationManager.IMPORTANCE_NONE
        }

    /** Whether `POST_NOTIFICATIONS` is granted; always true below Android 13, where it doesn't exist. */
    public val hasPermission: Boolean
        get() =
            Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED

    /**
     * A notification on the channel: [copy] as title, text and subtext, [actions] as buttons, [contentIntent] on
     * tap and [deleteIntent] when the user swipes it away. The app turns the delete into a snooze or a skip.
     * [silent] posts this one without the channel's sound and vibration (the app's own sounds-off setting).
     */
    public fun build(
        copy: Copy,
        @DrawableRes smallIcon: Int,
        actions: List<Action> = emptyList(),
        contentIntent: PendingIntent? = null,
        deleteIntent: PendingIntent? = null,
        autoCancel: Boolean = true,
        onlyAlertOnce: Boolean = true,
        silent: Boolean = false,
    ): Notification {
        val builder =
            NotificationCompat
                .Builder(context, channelId)
                .setSmallIcon(smallIcon)
                .setContentTitle(copy.title)
                .setContentText(copy.text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(copy.text))
                .setCategory(NotificationCompat.CATEGORY_REMINDER)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(autoCancel)
                .setOnlyAlertOnce(onlyAlertOnce)
                .setSilent(silent)
        copy.subtext?.let(builder::setSubText)
        contentIntent?.let(builder::setContentIntent)
        deleteIntent?.let(builder::setDeleteIntent)
        for (action in actions) builder.addAction(action.icon, action.title, action.intent)
        return builder.build()
    }

    /** Posts [notification] as [id]; false when the app may not post (then nothing happens). */
    public fun show(
        id: Int,
        notification: Notification,
    ): Boolean {
        if (!canPost) return false
        try {
            manager.notify(id, notification)
        } catch (_: SecurityException) {
            return false
        }
        return true
    }

    public fun cancel(id: Int) {
        manager.cancel(id)
    }
}
