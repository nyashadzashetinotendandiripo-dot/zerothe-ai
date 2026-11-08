package ai.zerothebot.app.accessibility

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Captures active notifications so Silent Mode can read and reply to them
 * without ever opening the target app or changing what's on screen.
 */
class ZerotheBotNotificationListener : NotificationListenerService() {

    override fun onListenerConnected() {
        NotificationStore.connected = true
        NotificationStore.listenerRef = this
        try {
            activeNotifications?.let { NotificationStore.replaceAll(it) }
        } catch (e: Exception) { }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        NotificationStore.put(sbn)
        NotificationStore.connected = true
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        NotificationStore.remove(sbn.key)
    }

    override fun onDestroy() {
        NotificationStore.connected = false
        NotificationStore.listenerRef = null
        super.onDestroy()
    }

    companion object {
        /** Best-effort dismissal from outside the service instance. */
        fun cancel(key: String): Boolean {
            val svc = NotificationStore.listenerRef ?: return false
            return try {
                svc.cancelNotification(key); true
            } catch (e: Exception) { false }
        }
    }
}

/** Keeps the last ~64 active notifications in memory for the RN bridge. */
object NotificationStore {
    private val lock = ReentrantLock()
    private val items = LinkedHashMap<String, StatusBarNotification>()
    @Volatile var connected = false

    val snapshotForService get() = Unit

    var listenerRef: ZerotheBotNotificationListener? = null

    fun all(): List<StatusBarNotification> = lock.withLock { ArrayList(items.values) }

    fun get(key: String): StatusBarNotification? = lock.withLock { items[key] }

    fun put(sbn: StatusBarNotification) = lock.withLock {
        items[sbn.key] = sbn
        while (items.size > 64) {
            val it = items.entries.iterator()
            if (it.hasNext()) { it.next(); it.remove() } else break
        }
    }

    fun remove(key: String) = lock.withLock { items.remove(key) }

    fun replaceAll(list: Array<StatusBarNotification>) = lock.withLock {
        items.clear()
        for (s in list) items[s.key] = s
    }
}
