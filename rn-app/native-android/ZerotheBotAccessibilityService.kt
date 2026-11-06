package ai.zerothebot.app.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.Intent
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.KeyEvent
import android.graphics.Path
import android.os.Bundle

class ZerotheBotAccessibilityService : AccessibilityService() {

    private val handler = Handler(Looper.getMainLooper())
    private var lastEvent: AccessibilityEvent? = null

    override fun onCreate() {
        super.onCreate()
        ServiceHolder.instance = this
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
        // nothing to do
    }

    override fun onDestroy() {
        ServiceHolder.instance = null
        super.onDestroy()
    }

    private fun boundsOf(node: AccessibilityNodeInfo): Rect {
        val r = Rect()
        node.getBoundsInScreen(r)
        return r
    }

    // ---------- Public API for the RN bridge ----------

    fun getRootNodeAsJson(): String? {
        return try {
            val root = rootInActiveWindow ?: return null
            nodeToJson(root, 0)
        } catch (e: Exception) {
            null
        }
    }

    fun tapNode(node: AccessibilityNodeInfo): Boolean {
        return try {
            node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        } catch (e: Exception) {
            false
        }
    }

    fun setNodeText(node: AccessibilityNodeInfo, text: String): Boolean {
        return try {
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        } catch (e: Exception) {
            false
        }
    }

    fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long): Boolean {
        return try {
            val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
            val duration = durationMs.coerceIn(100L, 10000L)
            val gesture = GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(path, 0, duration))
                .build()
            dispatchGesture(gesture, null, null)
        } catch (e: Exception) {
            false
        }
    }

    fun keyEvent(keyCode: Int): Boolean {
        return try {
            performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME.takeIf { keyCode == KeyEvent.KEYCODE_HOME }
                ?: AccessibilityService.GLOBAL_ACTION_BACK.takeIf { keyCode == KeyEvent.KEYCODE_BACK }
                ?: AccessibilityService.GLOBAL_ACTION_RECENTS.takeIf { keyCode == KeyEvent.KEYCODE_MENU }
                ?: return false)
        } catch (e: Exception) {
            false
        }
    }

    fun typeText(text: String): Boolean {
        val focus = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return false
        return setNodeText(focus, text)
    }

    fun findTextOnScreen(searchText: String, exact: Boolean): String {
        val results = StringBuilder("[")
        val root = rootInActiveWindow ?: return "[]"
        collectNodesMatching(root, searchText, exact, results)
        results.append("]")
        return results.toString()
    }

    private fun collectNodesMatching(node: AccessibilityNodeInfo?, search: String, exact: Boolean, out: StringBuilder) {
        if (node == null) return
        val text = node.text?.toString() ?: ""
        val desc = node.contentDescription?.toString() ?: ""
        val matchText = if (exact) text == search else text.contains(search, ignoreCase = true)
        val matchDesc = if (exact) desc == search else desc.contains(search, ignoreCase = true)
        if (matchText || matchDesc) {
            val b = boundsOf(node)
            if (out.length > 1) out.append(",")
            out.append("{\"text\":\"").append(escapeJson(text))
            out.append("\",\"desc\":\"").append(escapeJson(desc))
            out.append("\",\"id\":\"").append(escapeJson(node.viewIdResourceName ?: ""))
            out.append("\",\"class\":\"").append(escapeJson(node.className?.toString() ?: ""))
            out.append("\",\"clickable\":").append(node.isClickable)
            out.append(",\"x\":").append((b.left + b.right) / 2)
            out.append(",\"y\":").append((b.top + b.bottom) / 2)
            out.append(",\"w\":").append(b.right - b.left)
            out.append(",\"h\":").append(b.bottom - b.top)
            out.append("}")
        }
        for (i in 0 until node.childCount) {
            collectNodesMatching(node.getChild(i), search, exact, out)
        }
    }

    // ---------- Helpers called by the RN module ----------

    fun findAndTapByText(searchText: String): Boolean {
        val root = rootInActiveWindow ?: return false
        val node = findNodeByText(root, searchText)
        return node != null && tapNode(node)
    }

    private fun findNodeByText(node: AccessibilityNodeInfo, search: String): AccessibilityNodeInfo? {
        val text = node.text?.toString() ?: ""
        val desc = node.contentDescription?.toString() ?: ""
        if (text.contains(search, ignoreCase = true) || desc.contains(search, ignoreCase = true)) {
            if (node.isClickable) return node
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findNodeByText(child, search)
            if (found != null) return found
        }
        return null
    }

    fun tapAt(x: Float, y: Float): Boolean {
        val path = Path().apply { moveTo(x, y); lineTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 60))
            .build()
        return dispatchGesture(gesture, null, null)
    }

    fun longPressAt(x: Float, y: Float, durationMs: Long): Boolean {
        val path = Path().apply { moveTo(x, y); lineTo(x, y) }
        val duration = durationMs.coerceIn(500L, 5000L)
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, duration))
            .build()
        return dispatchGesture(gesture, null, null)
    }

    fun scrollScreen(direction: String, amount: Float): Boolean {
        val root = rootInActiveWindow ?: return false
        val b = boundsOf(root)
        val cx = (b.left + b.right) / 2f
        val cy = (b.top + b.bottom) / 2f
        val w = (b.right - b.left).toFloat()
        val h = (b.bottom - b.top).toFloat()
        val dist = amount.coerceIn(0.05f, 1.0f)
        val dx: Float
        val dy: Float
        when (direction) {
            "up" -> { dx = 0f; dy = -h * dist }
            "left" -> { dx = -w * dist; dy = 0f }
            "right" -> { dx = w * dist; dy = 0f }
            else -> { dx = 0f; dy = h * dist }
        }
        val dur = maxOf(w, h).times(2).toLong().coerceIn(400L, 2000L)
        return swipe(cx - dx / 2, cy - dy / 2, cx + dx / 2, cy + dy / 2, dur)
    }

    fun launchApp(packageName: String): Boolean {
        return try {
            val intent = packageManager.getLaunchIntentForPackage(packageName)
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
        val ev = lastEvent
        return ev?.packageName?.toString()
    }

    private fun nodeToJson(node: AccessibilityNodeInfo?, depth: Int): String {
        if (node == null || depth > 12) return ""
        val sb = StringBuilder()
        val b = boundsOf(node)
        sb.append("{\"text\":\"").append(escapeJson(node.text?.toString() ?: ""))
        sb.append("\",\"desc\":\"").append(escapeJson(node.contentDescription?.toString() ?: ""))
        sb.append("\",\"id\":\"").append(escapeJson(node.viewIdResourceName ?: ""))
        sb.append("\",\"class\":\"").append(escapeJson(node.className?.toString() ?: ""))
        sb.append("\",\"clickable\":").append(node.isClickable)
        sb.append(",\"x\":").append((b.left + b.right) / 2)
        sb.append(",\"y\":").append((b.top + b.bottom) / 2)
        sb.append(",\"w\":").append(b.right - b.left)
        sb.append(",\"h\":").append(b.bottom - b.top)
        sb.append(",\"children\":[")
        var first = true
        for (i in 0 until node.childCount) {
            val childJson = nodeToJson(node.getChild(i), depth + 1)
            if (childJson.isNotEmpty()) {
                if (!first) sb.append(",")
                sb.append(childJson)
                first = false
            }
        }
        sb.append("]}")
        return sb.toString()
    }

    private fun escapeJson(s: String): String {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    }

    companion object {
        const val SERVICE_NAME = "ai.zerothebot.app.accessibility.ZerotheBotAccessibilityService"
    }
}
