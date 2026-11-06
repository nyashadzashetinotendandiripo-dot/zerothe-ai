package ai.zerothebot.app.accessibility

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.TextView

/**
 * Adds a small floating "ZB" bubble overlay, then finishes immediately so only
 * the overlay remains. Tapping the bubble returns to ZerotheBot.
 */
class FloatingBubbleActivity : Activity() {

    private var windowManager: WindowManager? = null
    private var bubbleView: View? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        val tv = TextView(this).apply {
            text = "ZB"
            setTextColor(Color.WHITE)
            textSize = 14f
            gravity = Gravity.CENTER
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.parseColor("#1a73e8"))
            }
        }

        val params = WindowManager.LayoutParams(
            120, 120,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 40
            y = 300
        }

        tv.setOnTouchListener { v, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_UP -> {
                    val intent = packageManager.getLaunchIntentForPackage(packageName)
                    if (intent != null) {
                        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                        startActivity(intent)
                    }
                    removeBubble()
                    v.performClick()
                    true
                }
                else -> false
            }
        }

        try {
            windowManager?.addView(tv, params)
            bubbleView = tv
        } catch (e: Exception) {
            // no overlay permission — fail quietly
        }
        finish()
    }

    private fun removeBubble() {
        try { bubbleView?.let { windowManager?.removeView(it) } } catch (e: Exception) {}
        bubbleView = null
    }

    override fun onDestroy() {
        removeBubble()
        super.onDestroy()
    }
}
