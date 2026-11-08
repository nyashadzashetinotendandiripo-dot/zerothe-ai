package ai.zerothebot.app.accessibility

import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.view.KeyEvent
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReadableMap
import com.facebook.react.ReactPackage
import com.facebook.react.bridge.NativeModule
import com.facebook.react.uimanager.ViewManager

class AccessibilityPackage : ReactPackage {
    override fun createNativeModules(reactContext: ReactApplicationContext): List<NativeModule> =
        listOf(AccessibilityModule(reactContext), SilentControlModule(reactContext))

    override fun createViewManagers(reactContext: ReactApplicationContext): List<ViewManager<*, *>> =
        emptyList()
}

class AccessibilityModule(reactContext: ReactApplicationContext) : ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "ZerotheBotAccessibility"

    private fun svc(): ZerotheBotAccessibilityService? = ServiceHolder.instance

    @ReactMethod
    fun isAccessibilityEnabled(promise: Promise) {
        try {
            val manager = reactApplicationContext.getSystemService(Context.ACCESSIBILITY_SERVICE) as android.view.accessibility.AccessibilityManager
            val services = manager.getEnabledAccessibilityServiceList(android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
            val us = services.any { it.id?.contains("ai.zerothebot.app") == true }
            promise.resolve(us)
        } catch (e: Exception) {
            promise.resolve(false)
        }
    }

    @ReactMethod
    fun openAccessibilitySettings(promise: Promise) {
        try {
            val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            reactApplicationContext.startActivity(intent)
            promise.resolve(true)
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message)
        }
    }

    @ReactMethod
    fun isOverlayEnabled(promise: Promise) {
        promise.resolve(Settings.canDrawOverlays(reactApplicationContext))
    }

    @ReactMethod
    fun requestOverlayPermission(promise: Promise) {
        try {
            val intent = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:${reactApplicationContext.packageName}"))
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            reactApplicationContext.startActivity(intent)
            promise.resolve(true)
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message)
        }
    }

    @ReactMethod
    fun sendCommand(command: String, args: ReadableMap?, promise: Promise) {
        val s = svc()
        if (s == null) {
            promise.reject("NO_SERVICE", "Accessibility service not running. Enable ZerotheBot in Android Settings > Accessibility.")
            return
        }
        val a = args ?: com.facebook.react.bridge.Arguments.createMap()
        try {
            when (command) {
                "getRootNode" -> promise.resolve(s.getRootNodeAsJson())
                "tapText" -> promise.resolve(s.findAndTapByText(a.getString("text") ?: ""))
                "tapCoords" -> promise.resolve(s.tapAt((a.getDouble("x") ?: 0.0).toFloat(), (a.getDouble("y") ?: 0.0).toFloat()))
                "longPress" -> promise.resolve(s.longPressAt((a.getDouble("x") ?: 0.0).toFloat(), (a.getDouble("y") ?: 0.0).toFloat(), ((a.getDouble("durationMs") ?: 800.0)).toLong()))
                "setText" -> promise.resolve(s.typeText(a.getString("text") ?: ""))
                "swipe" -> promise.resolve(s.swipe(
                    (a.getDouble("x1") ?: 0.0).toFloat(), (a.getDouble("y1") ?: 0.0).toFloat(),
                    (a.getDouble("x2") ?: 0.0).toFloat(), (a.getDouble("y2") ?: 0.0).toFloat(),
                    ((a.getDouble("durationMs") ?: 800.0)).toLong()))
                "key" -> {
                    val code = when (a.getString("key")) {
                        "back" -> KeyEvent.KEYCODE_BACK
                        "home" -> KeyEvent.KEYCODE_HOME
                        "menu" -> KeyEvent.KEYCODE_MENU
                        else -> KeyEvent.KEYCODE_BACK
                    }
                    promise.resolve(s.keyEvent(code))
                }
                "findText" -> promise.resolve(s.findTextOnScreen(a.getString("text") ?: "", if (a.hasKey("exact")) a.getBoolean("exact") else false))
                "scroll" -> promise.resolve(s.scrollScreen(a.getString("direction") ?: "down", (a.getDouble("amount") ?: 0.6).toFloat()))
                "openApp" -> promise.resolve(s.launchApp(a.getString("app") ?: ""))
                "currentApp" -> promise.resolve(s.getCurrentPackageName())
                else -> promise.reject("UNKNOWN_COMMAND", "Unknown command: $command")
            }
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message ?: "error")
        }
    }
}
