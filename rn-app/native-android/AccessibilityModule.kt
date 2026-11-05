package ai.zerothebot.app.accessibility

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.WritableMap
import com.facebook.react.modules.core.DeviceEventManagerModule
import com.facebook.react.bridge.ReadableMap
import java.util.*

class AccessibilityModule(reactContext: ReactApplicationContext) : ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "ZerotheBotAccessibility"

    private fun customSvc(): ZerotheBotAccessibilityService? {
        return ServiceHolder.instance
    }

    // ---------- Permission checks ----------
    @ReactMethod
    fun isAccessibilityEnabled(promise: Promise) {
        val manager = reactApplicationContext.getSystemService(Context.ACCESSIBILITY_SERVICE) as android.accessibilityservice.AccessibilityManager
        val enabled = manager.isEnabled
        val services = manager.getEnabledAccessibilityServiceList(android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
        val us = services.any { it.id?.contains("ai.zerothebot.app") == true }
        promise.resolve(mapOf("enabled" to enabled, "ourServiceEnabled" to us))
    }

    @ReactMethod
    fun openAccessibilitySettings(promise: Promise) {
        val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        reactApplicationContext.startActivity(intent)
        promise.resolve(true)
    }

    @ReactMethod
    fun isOverlayEnabled(promise: Promise) {
        val can = Settings.canDrawOverlays(reactApplicationContext)
        promise.resolve(can)
    }

    @ReactMethod
    fun requestOverlayPermission(promise: Promise) {
        val intent = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${reactApplicationContext.packageName}"))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        reactApplicationContext.startActivity(intent)
        promise.resolve(true)
    }

    // ---------- Service communication ----------
    @ReactMethod
    fun sendCommand(command: String, args: ReadableMap, promise: Promise) {
        val svc = customSvc()
        if (svc == null) {
            promise.reject("NO_SERVICE", "Accessibility service not running. Enable it in Android Settings > Accessibility.")
            return
        }
        try {
            val result = when (command) {
                "getRootNode" -> svc.getRootNodeAsJson()
                "tapNode" -> {
                    val id = args.getString("id") ?: ""
                    svc.findAndTapById(id)
                    null
                }
                "tapCoords" -> {
                    val x = args.getDouble("x") ?: 0.0
                    val y = args.getDouble("y") ?: 0.0
                    svc.tapAt(x.toFloat(), y.toFloat())
                    null
                }
                "longPress" -> {
                    val x = args.getDouble("x") ?: 0.0
                    val y = args.getDouble("y") ?: 0.0
                    val dur = (args.getDouble("durationMs") ?: 800).toLong()
                    svc.longPressAt(x.toFloat(), y.toFloat(), dur)
                    null
                }
                "setText" -> {
                    val text = args.getString("text") ?: ""
                    svc.typeText(text)
                    null
                }
                "swipe" -> {
                    val x1 = args.getDouble("x1") ?: 0.0
                    val y1 = args.getDouble("y1") ?: 0.0
                    val x2 = args.getDouble("x2") ?: 0.0
                    val y2 = args.getDouble("y2") ?: 0.0
                    val dur = (args.getDouble("durationMs") ?: 800).toLong()
                    svc.swipe(x1.toFloat(), y1.toFloat(), x2.toFloat(), y2.toFloat(), dur)
                    null
                }
                "key" -> {
                    val key = args.getString("key") ?: ""
                    val code = when (key) {
                        "back" -> android.view.KeyEvent.KEYCODE_BACK
                        "home" -> android.view.KeyEvent.KEYCODE_HOME
                        "enter" -> android.view.KeyEvent.KEYCODE_ENTER
                        "menu" -> android.view.KeyEvent.KEYCODE_MENU
                        else -> android.view.KeyEvent.KEYCODE_UNKNOWN
                    }
                    svc.keyEvent(code)
                    null
                }
                "findText" -> {
                    val text = args.getString("text") ?: ""
                    val exact = args.getBoolean("exact") ?: false
                    svc.findTextOnScreen(text, exact)
                }
                "scroll" -> {
                    val direction = args.getString("direction") ?: "down"
                    val amount = (args.getDouble("amount") ?: 0.6).toFloat()
                    svc.scrollScreen(direction, amount)
                    null
                }
                "openApp" -> {
                    val app = args.getString("app") ?: ""
                    svc.launchApp(app)
                    null
                }
                "currentApp" -> {
                    svc.getCurrentPackageName()
                }
                else -> {
                    promise.reject("UNKNOWN_COMMAND", "Unknown command: $command")
                    return
                }
            }
            if (result == null) {
                promise.resolve(mapOf("ok" to true))
            } else {
                promise.resolve(result)
            }
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message ?: "Unknown error")
        }
    }

    @ReactMethod
    fun emitEvent(eventName: String, body: ReadableMap) {
        val params = Arguments.createMap()
        body.keySetIterator().forEachRemaining { key ->
            val value = body.getMap(key) ?: Arguments.createMap()
            params.pushMap(key, value)
        }
        reactApplicationContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit(eventName, params)
    }

    companion object {
        private var instance: AccessibilityModule? = null
        fun init(context: ReactApplicationContext) {
            instance = AccessibilityModule(context)
        }
        fun get(): AccessibilityModule? = instance
    }
}
