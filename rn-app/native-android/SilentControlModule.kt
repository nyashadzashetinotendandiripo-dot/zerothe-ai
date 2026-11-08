package ai.zerothebot.app.accessibility

import android.app.NotificationManager
import android.content.ContentProviderOperation
import android.content.Intent
import android.media.AudioManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.service.notification.StatusBarNotification
import android.provider.CalendarContract
import android.provider.ContactsContract
import android.provider.Settings
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.Arguments
import org.json.JSONArray
import org.json.JSONObject
import java.util.TimeZone

/**
 * Silent Mode — bots operate Android through official APIs so the user's screen
 * NEVER changes while they stay inside ZerotheBot.
 *
 *   - Read notifications        (NotificationListenerService, no app opens)
 *   - Reply to messages         (hidden RemoteInput reply, no app opens)
 *   - Alarms and timers         (AlarmClock intents with SKIP_UI, no app opens)
 *   - Calendar events           (CalendarContract, no app opens)
 *   - Contacts add/find         (ContactsContract, no app opens)
 *   - Volume / Do Not Disturb   (AudioManager / zen modes, no app opens)
 */
class SilentControlModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "ZerotheBotSilent"

    private fun ctx() = reactApplicationContext

    private fun notifMgr() =
        ctx().getSystemService(android.content.Context.NOTIFICATION_SERVICE) as NotificationManager

    private fun audioMgr() =
        ctx().getSystemService(android.content.Context.AUDIO_SERVICE) as AudioManager

    // ------------------------------------------------------------- status

    private fun isListenerEnabled(): Boolean {
        return try {
            val raw = Settings.Secure.getString(ctx().contentResolver, "enabled_notification_listeners") ?: ""
            raw.contains(ctx().packageName)
        } catch (e: Exception) { false }
    }

    @ReactMethod
    fun isSilentReady(promise: Promise) {
        try {
            val m = Arguments.createMap()
            m.putBoolean("listener", isListenerEnabled())
            m.putBoolean("dndAccess", notifMgr().isNotificationPolicyAccessGranted)
            promise.resolve(m)
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message)
        }
    }

    @ReactMethod
    fun openNotificationAccessSettings(promise: Promise) {
        try {
            val i = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ctx().startActivity(i)
            promise.resolve(true)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun openDndSettings(promise: Promise) {
        try {
            val i = Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ctx().startActivity(i)
            promise.resolve(true)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    // ------------------------------------------------------------- notifications

    @ReactMethod
    fun listNotifications(promise: Promise) {
        try {
            if (!isListenerEnabled()) {
                promise.reject("NO_LISTENER", "Notification access is off. Open Settings > Notification access > ZerotheBot and allow it.")
                return
            }
            val pm = ctx().packageManager
            val out = JSONArray()
            for (sbn in NotificationStore.all()) {
                try {
                    val o = JSONObject()
                    o.put("key", sbn.key)
                    o.put("pkg", sbn.packageName)
                    var appLabel = sbn.packageName
                    try {
                        appLabel = pm.getApplicationLabel(pm.getApplicationInfo(sbn.packageName, 0))?.toString() ?: appLabel
                    } catch (e: Exception) { }
                    o.put("app", appLabel)
                    val n = sbn.notification
                    val ex = n.extras
                    o.put("title", ex?.getCharSequence(android.app.Notification.EXTRA_TITLE)?.toString() ?: "")
                    o.put("text", ex?.getCharSequence(android.app.Notification.EXTRA_TEXT)?.toString() ?: "")
                    val big = ex?.getCharSequence(android.app.Notification.EXTRA_BIG_TEXT)?.toString()
                    if (!big.isNullOrEmpty()) o.put("big", big)
                    var replyable = false
                    for (a in n.actions ?: arrayOf()) {
                        if (!a.remoteInputs.isNullOrEmpty()) { replyable = true; break }
                    }
                    o.put("replyable", replyable)
                    o.put("when", sbn.postTime)
                    out.put(o)
                } catch (e: Exception) { /* skip malformed */ }
            }
            promise.resolve(out.toString())
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    /** Hidden direct reply: fills the RemoteInput of the notification's reply action.
     *  The target app never opens — the user stays inside ZerotheBot. */
    @ReactMethod
    fun replyToNotification(key: String, text: String, promise: Promise) {
        try {
            if (!isListenerEnabled()) {
                promise.reject("NO_LISTENER", "Notification access is off.")
                return
            }
            val sbn = NotificationStore.get(key)
            if (sbn == null) {
                promise.reject("NOT_FOUND", "Notification not found. List notifications first.")
                return
            }
            val n = sbn.notification
            var action: android.app.Notification.Action? = null
            for (a in n.actions ?: arrayOf()) {
                if (!a.remoteInputs.isNullOrEmpty()) { action = a; break }
            }
            if (action == null) {
                promise.reject("NO_REPLY", "This notification has no reply action.")
                return
            }
            val intent = Intent()
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            val results = Bundle()
            for (ri in action.remoteInputs) results.putCharSequence(ri.resultKey, text)
            android.app.RemoteInput.addResultsToIntent(action.remoteInputs, intent, results)
            action.actionIntent.send(ctx(), 0, intent)
            val o = Arguments.createMap()
            o.putBoolean("replied", true)
            promise.resolve(o)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun dismissNotification(key: String, promise: Promise) {
        try {
            val sbn = NotificationStore.get(key)
            if (sbn == null) { promise.reject("NOT_FOUND", "Notification not found."); return }
            val mgr = NotificationManagerCompatShim()
            promise.resolve(mgr.dismiss(this, sbn))
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    private inner class NotificationManagerCompatShim {
        fun dismiss(mod: SilentControlModule, sbn: StatusBarNotification): Boolean {
            return ZerotheBotNotificationListener.cancel(sbn.key)
        }
    }

    // ------------------------------------------------------------- alarms & timers

    /** Sets an alarm completely silently — the Clock app never opens. */
    @ReactMethod
    fun setAlarm(hour: Double, minute: Double, label: String, promise: Promise) {
        try {
            val i = Intent(android.provider.AlarmClock.ACTION_SET_ALARM)
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            i.putExtra(android.provider.AlarmClock.EXTRA_HOUR, hour.toInt())
            i.putExtra(android.provider.AlarmClock.EXTRA_MINUTES, minute.toInt())
            if (label.isNotBlank()) i.putExtra(android.provider.AlarmClock.EXTRA_MESSAGE, label)
            i.putExtra(android.provider.AlarmClock.EXTRA_SKIP_UI, true)
            ctx().startActivity(i)
            val o = Arguments.createMap()
            o.putBoolean("set", true); o.putInt("hour", hour.toInt()); o.putInt("minute", minute.toInt())
            promise.resolve(o)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    /** Starts a countdown completely silently — the Clock app never opens. */
    @ReactMethod
    fun setTimer(seconds: Double, label: String, promise: Promise) {
        try {
            val i = Intent(android.provider.AlarmClock.ACTION_SET_TIMER)
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            i.putExtra(android.provider.AlarmClock.EXTRA_LENGTH, seconds.toInt())
            if (label.isNotBlank()) i.putExtra(android.provider.AlarmClock.EXTRA_MESSAGE, label)
            i.putExtra(android.provider.AlarmClock.EXTRA_SKIP_UI, true)
            ctx().startActivity(i)
            val o = Arguments.createMap()
            o.putBoolean("set", true); o.putInt("seconds", seconds.toInt())
            promise.resolve(o)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    // ------------------------------------------------------------- volume / dnd

    @ReactMethod
    fun setVolume(percent: Double, stream: String, promise: Promise) {
        try {
            val am = audioMgr()
            val s = when (stream) {
                "ring" -> AudioManager.STREAM_RING
                "alarm" -> AudioManager.STREAM_ALARM
                "notification" -> AudioManager.STREAM_NOTIFICATION
                else -> AudioManager.STREAM_MUSIC
            }
            val max = am.getStreamMaxVolume(s)
            val vol = (max * (percent / 100.0)).toInt().coerceIn(0, max)
            am.setStreamVolume(s, vol, 0)
            val o = Arguments.createMap()
            o.putInt("volume", vol); o.putInt("max", max)
            promise.resolve(o)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun dnd(minutes: Double, promise: Promise) {
        try {
            if (!notifMgr().isNotificationPolicyAccessGranted) {
                promise.reject("NO_DND", "Do Not Disturb access is off. Grant it once in Settings.")
                return
            }
            notifMgr().setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_NONE)
            if (minutes > 0) {
                Handler(Looper.getMainLooper()).postDelayed({
                    try { notifMgr().setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_ALL) } catch (e: Exception) { }
                }, (minutes * 60.0 * 1000.0).toLong())
            }
            promise.resolve(true)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun dndOff(promise: Promise) {
        try {
            if (!notifMgr().isNotificationPolicyAccessGranted) {
                promise.reject("NO_DND", "Do Not Disturb access is off. Grant it once in Settings.")
                return
            }
            notifMgr().setInterruptionFilter(NotificationManager.INTERRUPTION_FILTER_ALL)
            promise.resolve(true)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    // ------------------------------------------------------------- contacts

    @ReactMethod
    fun addContact(name: String, phone: String, promise: Promise) {
        try {
            val ops = ArrayList<ContentProviderOperation>()
            ops.add(ContentProviderOperation.newInsert(ContactsContract.RawContacts.CONTENT_URI)
                .withValue(ContactsContract.RawContacts.ACCOUNT_TYPE, null)
                .withValue(ContactsContract.RawContacts.ACCOUNT_NAME, null).build())
            ops.add(ContentProviderOperation.newInsert(ContactsContract.Data.CONTENT_URI)
                .withValueBackReference(ContactsContract.Data.RAW_CONTACT_ID, 0)
                .withValue(ContactsContract.Data.MIMETYPE, ContactsContract.CommonDataKinds.StructuredName.CONTENT_ITEM_TYPE)
                .withValue(ContactsContract.CommonDataKinds.StructuredName.DISPLAY_NAME, name).build())
            ops.add(ContentProviderOperation.newInsert(ContactsContract.Data.CONTENT_URI)
                .withValueBackReference(ContactsContract.Data.RAW_CONTACT_ID, 0)
                .withValue(ContactsContract.Data.MIMETYPE, ContactsContract.CommonDataKinds.Phone.CONTENT_ITEM_TYPE)
                .withValue(ContactsContract.CommonDataKinds.Phone.NUMBER, phone)
                .withValue(ContactsContract.CommonDataKinds.Phone.TYPE, ContactsContract.CommonDataKinds.Phone.TYPE_MOBILE).build())
            ctx().contentResolver.applyBatch(ContactsContract.AUTHORITY, ops)
            promise.resolve(true)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message ?: "contact add failed") }
    }

    @ReactMethod
    fun findContact(name: String, promise: Promise) {
        try {
            val sel = "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ?"
            val args = arrayOf("%$name%")
            val out = JSONArray()
            ctx().contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER),
                sel, args, null
            )?.use { c ->
                var i = 0
                while (c.moveToNext() && i < 10) {
                    val o = JSONObject()
                    o.put("name", c.getString(0) ?: "")
                    o.put("number", c.getString(1) ?: "")
                    out.put(o); i++
                }
            }
            promise.resolve(out.toString())
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message ?: "contact lookup failed") }
    }

    // ------------------------------------------------------------- calendar

    @ReactMethod
    fun addCalendarEvent(title: String, startMs: Double, minutes: Double, promise: Promise) {
        try {
            val cr = ctx().contentResolver
            var calId = -1L
            cr.query(CalendarContract.Calendars.CONTENT_URI, arrayOf(CalendarContract.Calendars._ID), null, null, null)?.use { c ->
                if (c.moveToFirst()) calId = c.getLong(0)
            }
            if (calId < 0) { promise.reject("NO_CALENDAR", "No calendar found on this device."); return }
            val start = startMs.toLong()
            val end = start + (minutes.coerceAtLeast(1.0) * 60.0 * 1000.0).toLong()
            val v = android.content.ContentValues().apply {
                put(CalendarContract.Events.CALENDAR_ID, calId)
                put(CalendarContract.Events.TITLE, title)
                put(CalendarContract.Events.DTSTART, start)
                put(CalendarContract.Events.DTEND, end)
                put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
            }
            val uri = cr.insert(CalendarContract.Events.CONTENT_URI, v)
            val o = Arguments.createMap()
            o.putString("id", uri?.lastPathSegment ?: "")
            o.putString("tz", TimeZone.getDefault().id)
            promise.resolve(o)
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message ?: "calendar insert failed") }
    }
}
