package ai.zerothebot.app.accessibility

import android.content.Context
import android.graphics.ImageFormat
import android.hardware.display.DisplayManager
import android.media.ImageReader
import android.os.Build
import android.view.Display

/**
 * Hidden Screen — the bot's invisible workspace.
 *
 * Creates a *public virtual display* (an official, phone-agnostic Android API,
 * no vendor tricks, no root) that no user can see. Apps are launched onto it
 * with ActivityOptions.setLaunchDisplayId, and the AccessibilityService reads
 * and drives them there via getWindowsOnAllDisplays() (Android 11+).
 *
 * The display renders into an ImageReader we own, so the bot can later feed
 * its pixels to vision models without any screen-capture permission.
 *
 * Compatibility rule: everything here is a no-op / graceful-fail on phones
 * below Android 11 (API 30), where the system falls back to Silent Mode +
 * the visible robot finger.
 */
object HiddenDisplayController {

    private const val INVALID = -1

    var displayId: Int = INVALID
        private set

    private var virtualDisplay: android.hardware.display.VirtualDisplay? = null
    private var reader: ImageReader? = null

    fun supported(): Boolean = Build.VERSION.SDK_INT >= 30

    fun isActive(context: Context): Boolean {
        if (displayId == INVALID) return false
        return try {
            val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
            dm.getDisplay(displayId) != null
        } catch (e: Exception) { false }
    }

    /**
     * Create the invisible display. Safe to call repeatedly (idempotent).
     * Returns the display id, or -1 when unsupported/failed.
     */
    @Synchronized
    fun create(context: Context, width: Int, height: Int, dpi: Int): Int {
        if (displayId != INVALID && isActive(context)) return displayId
        close()
        if (!supported()) return INVALID
        try {
            val w = width.coerceIn(320, 2160)
            val h = height.coerceIn(480, 2160)
            val d = dpi.coerceIn(120, 640)
            val r = ImageReader.newInstance(w, h, ImageFormat.RGBA_8888, 2)
            reader = r
            val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
            val flags = DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC or
                        DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY
            val vd = dm.createVirtualDisplay(
                "ZerotheBotHidden", w, h, d, r.surface, flags)
            if (vd == null) {
                r.close(); reader = null
                return INVALID
            }
            virtualDisplay = vd
            displayId = vd.display.id
            return displayId
        } catch (e: Exception) {
            close()
            return INVALID
        }
    }

    @Synchronized
    fun close() {
        try { virtualDisplay?.release() } catch (e: Exception) { }
        try { reader?.close() } catch (e: Exception) { }
        virtualDisplay = null
        reader = null
        displayId = INVALID
    }
}
