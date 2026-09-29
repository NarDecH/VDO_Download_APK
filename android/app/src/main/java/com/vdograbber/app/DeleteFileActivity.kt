package com.vdograbber.app

import android.app.Activity
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import java.io.File

/**
 * v1.1.7: tiny trampoline the "ลบไฟล์" notification action opens, so deleting
 * the published download happens in a foreground component with user-visible
 * feedback (a Service cannot show a Toast reliably and MediaStore deletes on
 * Q+ belong to interactive contexts).
 *
 * Pass the MediaStore content URI (Q+), the raw file path (pre-Q), or both.
 */
class DeleteFileActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uri = intent.getStringExtra(EXTRA_URI).orEmpty()
        val raw = intent.getStringExtra(EXTRA_RAW).orEmpty()
        var ok = false
        try {
            ok = when {
                uri.isNotEmpty() -> contentResolver.delete(Uri.parse(uri), null, null) > 0
                raw.isNotEmpty() -> File(raw).delete()
                else -> false
            }
        } catch (e: Exception) {
            FileLog.app("ERROR", "dl", "delete failed: $e")
        }
        FileLog.event(
            if (ok) "download_deleted" else "download_delete_error",
            mapOf("uri" to uri.take(120), "path" to raw.take(200)),
        )
        Toast.makeText(this, if (ok) R.string.deleted else R.string.delete_failed, Toast.LENGTH_SHORT).show()
        finish()
    }

    companion object {
        const val EXTRA_URI = "uri"
        const val EXTRA_RAW = "raw"

        /** Immutable PendingIntent for the notification action. */
        fun pendingIntent(ctx: Context, uri: String, rawPath: String): PendingIntent =
            PendingIntent.getActivity(
                ctx, RC_DELETE,
                Intent(ctx, DeleteFileActivity::class.java)
                    .putExtra(EXTRA_URI, uri)
                    .putExtra(EXTRA_RAW, rawPath),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )

        private const val RC_DELETE = 47
    }
}
