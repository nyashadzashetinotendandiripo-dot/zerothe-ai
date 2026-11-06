package ai.zerothebot.app.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.view.KeyEvent
import android.graphics.Path
import android.os.Bundle
import android.content.ComponentName
import android.content.pm.PackageManager
import androidx.lifecycle.ProcessLifecycleOwner
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner

class ZerotheBotAccessibilityService : AccessibilityService(), DefaultLifecycleObserver {

    private val handler = Handler(Looper.getMainLooper())
    private var lastEvent: AccessibilityEvent? = null
    private var commandCallback: ((Map<String, Any?>) -> Unit)? = null
    private var screenCallback: ((String) -> Unit)? = null

    override fun onCreate() {
        super.onCreate()
        ServiceHolder.instance = this
        ProcessLifecycleOwner.get().lifecycle.addObserver(this)
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        val info = AccessibilityServiceInfo().apply {
            eventTypes = AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED or
                         AccessibilityEvent.TYPE_VIEW_FOCUSED or
                         AccessibilityEvent.TYPE_VIEW_CLICKED or
                         AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED or
                         AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            flags = AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS or
                    AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOW
        }
        serviceInfo = info
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        lastEvent = event
    }

    override fun onInterrupt() {
        // Service interrupted, nothing to do
    }

    override fun onDestroy() {
        ServiceHolder.instance = null
        ProcessLifecycleOwner.get().lifecycle.removeObserver(this)
        super.onDestroy()
    }

    override fun onCreate(owner: LifecycleOwner) { }
    override fun onStart(owner: LifecycleOwner) { }
    override fun onResume(owner: LifecycleOwner) { }
    override fun onPause(owner: LifecycleOwner) { }
    override fun onStop(owner: LifecycleOwner) { }
    override fun onDestroy(owner: LifecycleOwner) { }

    // ---------- Public API for RN bridge ----------
    fun setCommandCallback(cb: (Map<String, Any?>) -> Unit) {
        commandCallback = cb
    }

    fun setScreenCallback(cb: (String) -> Unit) {
        screenCallback = cb
    }

    fun getRootNodeAsJson(): String? {
        return try {
            val root = rootInActiveWindow
            if (root == null) null else nodeToJson(root)
        } catch (e: Exception) {
            null
        }
    }

    fun performGlobalActionNative(action: Int): Boolean {
        return try {
            performGlobalAction(action)
            true
        } catch (e: Exception) {
            false
        }
    }

    fun tapNode(node: AccessibilityNodeInfo): Boolean {
        return try {
            node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            true
        } catch (e: Exception) {
            false
        }
    }

    fun longPressNode(node: AccessibilityNodeInfo, durationMs: Long): Boolean {
        return try {
            node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
            true
        } catch (e: Exception) {
            false
        }
    }

    fun setNodeText(node: AccessibilityNodeInfo, text: String): Boolean {
        return try {
            val action = AccessibilityNodeInfo.AccessibilityAction(AccessibilityNodeInfo.ACTION_SET_TEXT, null)
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            node.performAction(action, args)
            true
        } catch (e: Exception) {
            false
        }
    }

    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        return try {
            val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
            val duration = durationMs.coerceAtLeast(300).coerceAtMost(5000)
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 100, duration))
                .build()
            dispatchGesture(gesture, null, null)
            true
        } catch (e: Exception) {
            false
        }
    }

    fun keyEvent(keyCode: Int): Boolean {
        return try {
            dispatchKeyEvent(KeyEvent(keyCode))
            true
        } catch (e: Exception) {
            false
        }
    }

    fun typeText(text: String): Boolean {
        val focus = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        return if (focus != null) setNodeText(focus, text) else false
    }

    fun findTextOnScreen(searchText: String, exact: Boolean = false): List<Map<String, Any?>> {
        val results = mutableListOf<Map<String, Any?>>()
        val root = rootInActiveWindow ?: return results
        collectNodesMatching(root, searchText, exact, results)
        return results
    }

    private fun collectNodesMatching(node: AccessibilityNodeInfo, search: String, exact: Boolean, out: MutableList<Map<String, Any?>>) {
        val text = node.text?.toString() ?: ""
        val desc = node.contentDescription?.toString() ?: ""
        val matchText = if (exact) text == search else text.contains(search, ignoreCase = true)
        val matchDesc = if (exact) desc == search else desc.contains(search, ignoreCase = true)
        if (matchText || matchDesc) {
            val bounds = node.boundsInScreen
            out.add(mapOf(
                "text" to text,
                "desc" to desc,
                "id" to node.viewIdResourceName ?: "",
                "class" to node.className ?: "",
                "clickable" to node.isClickable,
                "x" to ((bounds.left + bounds.right) / 2),
                "y" to ((bounds.top + bounds.bottom) / 2),
                "w" to (bounds.right - bounds.left),
                "h" to (bounds.bottom - bounds.top)
            ))
        }
        for (i in 0 until node.childCount) {
            collectNodesMatching(node.getChild(i), search, exact, out)
        }
    }

    // ---------- Helpers called by RN module ----------
    fun findAndTapById(id: String): Boolean {
        val root = rootInActiveWindow ?: return false
        return findAndTapByIdRecursive(root, id)
    }

    private fun findAndTapByIdRecursive(node: AccessibilityNodeInfo, id: String): Boolean {
        if (node.viewIdResourceName == id) {
            return tapNode(node)
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            if (findAndTapByIdRecursive(child, id)) return true
        }
        return false
    }

    fun tapAt(x: Float, y: Float): Boolean {
        val root = rootInActiveWindow ?: return false
        val node = findNodeAt(root, x, y)
        return if (node != null) tapNode(node) else false
    }

    private fun findNodeAt(node: AccessibilityNodeInfo, x: Float, y: Float): AccessibilityNodeInfo? {
        val bounds = node.boundsInScreen
        if (x >= bounds.left && x <= bounds.right && y >= bounds.top && y <= bounds.bottom) {
            // Check children first (deepest match)
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                val found = findNodeAt(child, x, y)
                if (found != null) return found
            }
            return node
        }
        return null
    }

    fun longPressAt(x: Float, y: Float, durationMs: Long): Boolean {
        val root = rootInActiveWindow ?: return false
        val node = findNodeAt(root, x, y)
        return if (node != null) longPressNode(node, durationMs) else false
    }

    fun scrollScreen(direction: String, amount: Float): Boolean {
        val root = rootInActiveWindow ?: return false
        val bounds = root.boundsInScreen
        val cx = (bounds.left + bounds.right) / 2f
        val cy = (bounds.top + bounds.bottom) / 2f
        val w = bounds.right - bounds.left
        val h = bounds.bottom - bounds.top
        val dist = (if (amount > 1f) 1f else amount).coerceAtLeast(0.05f)

        val (dx, dy) = when (direction) {
            "up" -> Pair(0f, -h * dist)
            "down" -> Pair(0f, h * dist)
            "left" -> Pair(-w * dist, 0f)
            "right" -> Pair(w * dist, 0f)
            else -> Pair(0f, h * dist)
        }

        return swipe(cx - dx / 2, cy - dy / 2, cx + dx / 2, cy + dy / 2, (max(w, h) * 4).toLong().coerceAtLeast(600))
    }

    fun launchApp(packageName: String): Boolean {
        return try {
            val pm = packageManager
            val intent = pm.getLaunchIntentForPackage(packageName)
            if (intent == null) false else {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                true
            }
        } catch (e: Exception) {
            false
        }
    }

    fun getCurrentPackageName(): String? {
        val events = lastEvent
        return events?.packageName?.toString()
    }

    private fun nodeToJson(node: AccessibilityNodeInfo, depth: Int = 0): String {
        if (depth > 12) return ""
        val sb = StringBuilder()
        val text = node.text?.toString() ?: ""
        val desc = node.contentDescription?.toString() ?: ""
        val id = node.viewIdResourceName ?: ""
        val clickable = node.isClickable
        val bounds = node.boundsInScreen
        sb.append("{")
        sb.append("\"text\":\"${escapeJson(text)}\",")
        sb.append("\"desc\":\"${escapeJson(desc)}\",")
        sb.append("\"id\":\"${escapeJson(id)}\",")
        sb.append("\"class\":\"${escapeJson(node.className ?: "")}\",")
        sb.append("\"clickable\":$clickable,")
        sb.append("\"x\":${(bounds.left + bounds.right) / 2},")
        sb.append("\"y\":${(bounds.top + bounds.bottom) / 2},")
        sb.append("\"w\":${bounds.right - bounds.left},")
        sb.append("\"h\":${bounds.bottom - bounds.top},")
        sb.append("\"children\":[")
        val children = mutableListOf<String>()
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            children.add(nodeToJson(child, depth + 1))
        }
        sb.append(children.joinToString(","))
        sb.append("]}")
        return sb.toString()
    }

    private fun escapeJson(s: String): String {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n")
    }

    companion object {
        const val SERVICE_NAME = "ai.zerothebot.app.accessibility.ZerotheBotAccessibilityService"
    }
}
