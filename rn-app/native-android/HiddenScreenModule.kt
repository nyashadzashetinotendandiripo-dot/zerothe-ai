package ai.zerothebot.app.accessibility

import android.os.Build
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.Arguments

/**
 * RN bridge for the Hidden Screen: the bot's invisible workspace where apps
 * run and get driven without ever appearing on the user's display.
 * Works on every Android 11+ phone; reports capability honestly otherwise.
 */
class HiddenScreenModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "ZerotheBotHidden"

    private fun ctx() = reactApplicationContext
    private fun svc() = ServiceHolder.instance

    @ReactMethod
    fun isHiddenAvailable(promise: Promise) {
        try {
            val m = Arguments.createMap()
            m.putBoolean("supported", HiddenDisplayController.supported())
            m.putBoolean("active", HiddenDisplayController.isActive(ctx()))
            m.putInt("displayId", HiddenDisplayController.displayId)
            m.putInt("api", Build.VERSION.SDK_INT)
            m.putBoolean("service", svc() != null)
            promise.resolve(m)
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message)
        }
    }

    /** Create the invisible display. Idempotent. Returns { displayId }. */
    @ReactMethod
    fun create(width: Double, height: Double, dpi: Double, promise: Promise) {
        try {
            if (svc() == null) {
                promise.reject("NO_SERVICE", "Enable ZerotheBot in Accessibility settings first.")
                return
            }
            val id = HiddenDisplayController.create(
                ctx(), width.toInt(), height.toInt(), dpi.toInt())
            val m = Arguments.createMap()
            m.putInt("displayId", id)
            m.putBoolean("ok", id != -1)
            promise.resolve(m)
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message)
        }
    }

    @ReactMethod
    fun close(promise: Promise) {
        try {
            HiddenDisplayController.close()
            promise.resolve(true)
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message)
        }
    }

    /** Launch an app onto the hidden display. The user's screen does not change. */
    @ReactMethod
    fun launchApp(pkg: String, promise: Promise) {
        try {
            val s = svc() ?: run {
                promise.reject("NO_SERVICE", "Accessibility service not running."); return
            }
            val id = HiddenDisplayController.displayId
            if (id == -1) {
                promise.reject("NO_DISPLAY", "Hidden screen is off. Create it first."); return
            }
            val ok = s.launchAppOnDisplay(ctx(), pkg, id)
            if (!ok) {
                promise.reject("LAUNCH_FAILED", "Could not launch '$pkg' on the hidden screen (app may refuse)."); return
            }
            val m = Arguments.createMap()
            m.putBoolean("launched", true); m.putInt("displayId", id)
            promise.resolve(m)
        } catch (e: Exception) {
            promise.reject("EXCEPTION", e.message)
        }
    }

    private fun activeDisplayId(): Int {
        val id = HiddenDisplayController.displayId
        if (id == -1) throw IllegalStateException("Hidden screen is off.")
        return id
    }

    @ReactMethod
    fun dump(promise: Promise) {
        try {
            val s = svc() ?: run { promise.reject("NO_SERVICE", "No accessibility service."); return }
            promise.resolve(s.getRootOnDisplay(activeDisplayId()))
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun findText(text: String, exact: Boolean, promise: Promise) {
        try {
            val s = svc() ?: run { promise.reject("NO_SERVICE", "No accessibility service."); return }
            promise.resolve(s.findTextOnDisplay(activeDisplayId(), text, exact))
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun tapText(text: String, promise: Promise) {
        try {
            val s = svc() ?: run { promise.reject("NO_SERVICE", "No accessibility service."); return }
            promise.resolve(s.tapTextOnDisplay(activeDisplayId(), text))
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun setText(search: String, text: String, promise: Promise) {
        try {
            val s = svc() ?: run { promise.reject("NO_SERVICE", "No accessibility service."); return }
            promise.resolve(s.setTextOnDisplay(activeDisplayId(), search, text))
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }

    @ReactMethod
    fun scroll(direction: String, promise: Promise) {
        try {
            val s = svc() ?: run { promise.reject("NO_SERVICE", "No accessibility service."); return }
            promise.resolve(s.scrollOnDisplay(activeDisplayId(), direction))
        } catch (e: Exception) { promise.reject("EXCEPTION", e.message) }
    }
}
