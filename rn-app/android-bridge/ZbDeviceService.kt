package ai.zerothebot.app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * ZerotheBot Device Bridge.
 *
 * An AccessibilityService that lets the user's Bots act on this phone: open apps,
 * tap, type and press system keys. The Bot queues a job on the server; this
 * service polls /api/device/pull, performs it and posts the result to
 * /api/device/done. No screen capture in this version.
 */
class ZbDeviceService : AccessibilityService() {

    @Volatile private var running = false
    private var worker: Thread? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        if (running) return
        running = true
        worker = Thread { loop() }.also { it.isDaemon = true; it.start() }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) { /* not used */ }

    override fun onInterrupt() { /* nothing to interrupt */ }

    override fun onDestroy() {
        running = false
        worker?.interrupt()
        worker = null
        super.onDestroy()
    }

    // ------------------------------------------------------------------ polling

    private fun loop() {
        while (running) {
            try {
                val job = pull()
                if (job != null) {
                    val id = job.optString("id")
                    try {
                        val out = execute(job)
                        done(id, out, true)
                    } catch (e: Exception) {
                        done(id, e.message ?: e.toString(), false)
                    }
                } else {
                    try { ping() } catch (_: Exception) { }
                    Thread.sleep(2500)
                }
            } catch (e: InterruptedException) {
                return
            } catch (e: Exception) {
                try { Thread.sleep(4000) } catch (ie: InterruptedException) { return }
            }
        }
    }

    private fun http(method: String, path: String, body: String? = null): String {
        val conn = (URL(ZbDeviceConfig.BASE + path).openConnection() as HttpURLConnection)
        conn.requestMethod = method
        conn.connectTimeout = 15000
        conn.readTimeout = 25000
        conn.setRequestProperty("Authorization", "Bearer " + ZbDeviceConfig.TOKEN)
        if (body != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val text = stream?.bufferedReader()?.use(BufferedReader::readText) ?: ""
        conn.disconnect()
        if (code !in 200..299) throw RuntimeException("HTTP $code: " + text.take(180))
        return text
    }

    private fun pull(): JSONObject? {
        val o = JSONObject(http("GET", "/api/device/pull?device=phone"))
        return o.optJSONObject("job")
    }

    private fun ping() {
        val info = JSONObject()
            .put("model", Build.MODEL)
            .put("sdk", Build.VERSION.SDK_INT)
            .put("app", "ZerotheBot")
        val body = JSONObject().put("device", "phone").put("info", info).toString()
        http("POST", "/api/device/ping", body)
    }

    private fun done(id: String, result: String, ok: Boolean) {
        if (id.isEmpty()) return
        val body = JSONObject()
            .put("id", id)
            .put("ok", ok)
            .put("result", result.take(4000))
            .toString()
        http("POST", "/api/device/done", body)
    }

    // ---------------------------------------------------------------- commands

    private fun execute(job: JSONObject): String {
        val cmd = job.optString("cmd")
        val args = job.optJSONObject("args") ?: JSONObject()
        return when (cmd) {
            "open_app" -> openApp(args.optString("app"))
            "tap" -> tap(args.optInt("x"), args.optInt("y"))
            "tap_text" -> tapText(args.optString("text"))
            "ui" -> dumpUi()
            "text" -> typeText(args.optString("text"))
            "key" -> pressKey(args.optString("key"))
            else -> throw IllegalArgumentException("Unknown command: $cmd")
        }
    }

    private fun openApp(name: String): String {
        val want = name.trim()
        if (want.isEmpty()) throw IllegalArgumentException("app is required.")
        val pm = packageManager
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(intent, 0)
        val lower = want.lowercase()
        var exact: String? = null
        var partial: String? = null
        for (ri in apps) {
            val pkg = ri.activityInfo.packageName
            val label = try { ri.loadLabel(pm).toString() } catch (_: Exception) { pkg }
            if (pkg.equals(want, true) || label.equals(want, true)) { exact = pkg; break }
            if (partial == null && (label.lowercase().contains(lower) || pkg.lowercase().contains(lower))) {
                partial = pkg
            }
        }
        val target = exact ?: partial ?: throw RuntimeException("No installed app matches '$want'.")
        val launch = pm.getLaunchIntentForPackage(target)
            ?: throw RuntimeException("Could not open $target.")
        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(launch)
        return "Opened $target on the phone."
    }

    private fun tap(x: Int, y: Int): String {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            throw RuntimeException("Tap needs Android 7 or newer.")
        }
        val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 70))
            .build()
        dispatchGesture(gesture, null, null)
        return "Tapped ($x, $y) on the phone."
    }

    private fun focusedField(): AccessibilityNodeInfo? {
        findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let { return it }
        val root = rootInActiveWindow ?: return null
        return findEditable(root)
    }

    private fun findEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isEditable) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findEditable(child)
            if (found != null) return found
        }
        return null
    }

    /** Readable dump of the current screen: text, class, clickable/editable and tap point. */
    private fun dumpUi(): String {
        val root = rootInActiveWindow ?: throw RuntimeException("No active window on the phone.")
        val out = StringBuilder()
        fun walk(node: AccessibilityNodeInfo, depth: Int) {
            val text = node.text?.toString()?.trim().orEmpty()
            val desc = node.contentDescription?.toString()?.trim().orEmpty()
            if (text.isNotEmpty() || desc.isNotEmpty() || node.isClickable || node.isEditable) {
                val r = Rect()
                node.getBoundsInScreen(r)
                repeat(minOf(depth, 8)) { out.append("  ") }
                if (text.isNotEmpty()) out.append('"').append(text.take(80)).append("\" ")
                if (desc.isNotEmpty()) out.append("desc=\"").append(desc.take(60)).append("\" ")
                out.append('[').append(node.className?.toString()?.substringAfterLast('.') ?: "?").append(']')
                if (node.isEditable) out.append(" EDIT")
                if (node.isClickable) out.append(" CLICK")
                out.append(" @").append(r.centerX()).append(',').append(r.centerY())
                out.append('\n')
            }
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                walk(child, depth + 1)
            }
        }
        walk(root, 0)
        return out.toString().ifEmpty { "The screen has no readable text." }.take(6000)
    }

    /** Find an on-screen element by its text and click it (falls back to a coordinate tap). */
    private fun tapText(text: String): String {
        val want = text.trim().lowercase()
        if (want.isEmpty()) throw IllegalArgumentException("text is required.")
        val root = rootInActiveWindow ?: throw RuntimeException("No active window on the phone.")
        fun find(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
            val label = ((node.text?.toString() ?: "") + " " + (node.contentDescription?.toString() ?: "")).lowercase()
            if (label.contains(want)) return node
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                find(child)?.let { return it }
            }
            return null
        }
        val node = find(root) ?: throw RuntimeException("No on-screen element matches '$text'.")
        var target: AccessibilityNodeInfo? = node
        while (target != null && !target.isClickable) target = target.parent
        val hit = target ?: node
        if (hit.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            return "Tapped '$text' on the phone."
        }
        val r = Rect()
        hit.getBoundsInScreen(r)
        return tap(r.centerX(), r.centerY())
    }

    private fun typeText(text: String): String {
        if (text.isEmpty()) throw IllegalArgumentException("text is required.")
        val node = focusedField()
        if (node != null) {
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
                return "Typed on the phone."
            }
        }
        val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("ZerotheBot", text))
        if (node != null) {
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            if (node.performAction(AccessibilityNodeInfo.ACTION_PASTE)) return "Typed on the phone."
        }
        throw RuntimeException("No text field is focused on the phone. Open the app and tap the field first.")
    }

    private fun pressKey(key: String): String {
        when (key.lowercase()) {
            "back" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "home" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "recents" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "enter" -> {
                val node = focusedField()
                if (node != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    val enter = AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER
                    if (node.performAction(enter.id)) return "Pressed enter on the phone."
                }
                performGlobalAction(GLOBAL_ACTION_BACK)
            }
            "backspace" -> {
                val node = focusedField() ?: throw RuntimeException("No text field is focused.")
                val cur = node.text?.toString() ?: ""
                val next = if (cur.isEmpty()) cur else cur.substring(0, cur.length - 1)
                val args = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, next)
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }
            else -> throw IllegalArgumentException("Unknown key: $key")
        }
        return "Pressed $key on the phone."
    }
}
