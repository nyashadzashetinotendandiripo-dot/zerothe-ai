package ai.zerothebot.app.accessibility

import android.app.Activity
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Bundle
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.ImageView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class FloatingBubbleActivity : AppCompatActivity(), View.OnTouchListener {

    private lateinit var windowManager: WindowManager
    private lateinit var bubbleView: View
    private lateinit var bubbleParams: WindowManager.LayoutParams

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Don't show a normal activity UI; we only create the overlay window
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager

        bubbleParams = WindowManager.LayoutParams().apply {
            type = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            } else {
                WindowManager.LayoutParams.TYPE_PHONE
            }
            flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                    WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            format = PixelFormat.TRANSLUCENT
            gravity = Gravity.TOP or Gravity.START
            x = 50
            y = 200
            width = 100
            height = 100
        }

        bubbleView = LayoutInflater.from(this).inflate(android.R.layout.simple_list_item_1, null)
        bubbleView.setBackgroundResource(android.R.color.holo_blue_bright)
        val tv = bubbleView.findViewById<android.widget.TextView>(android.R.id.text1)
        tv.text = "ZB"
        tv.setTextColor(android.graphics.Color.WHITE)
        bubbleView.setOnTouchListener(this)

        windowManager.addView(bubbleView, bubbleParams)

        bubbleView.setOnClickListener {
            // Return to ZerotheBot main activity
            val intent = Intent(this, MainActivity::class.java)
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            startActivity(intent)
            windowManager.removeView(bubbleView)
            finish()
        }

        // Finish the activity immediately so only the overlay remains
        finish()
    }

    override fun onTouch(v: View?, event: MotionEvent?): Boolean {
        if (event?.action == MotionEvent.ACTION_DOWN) {
            bubbleParams.x = event.rawX.toInt() - bubbleView.width / 2
            bubbleParams.y = event.rawY.toInt() - bubbleView.height / 2 - 50
            windowManager.updateViewLayout(bubbleView, bubbleParams)
        }
        return false
    }

    override fun onDestroy() {
        super.onDestroy()
        try { windowManager.removeView(bubbleView) } catch (_: Exception) {}
    }
}
