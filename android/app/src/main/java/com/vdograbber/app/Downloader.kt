package com.vdograbber.app

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import android.webkit.CookieManager

/**
 * Queue downloads through the platform DownloadManager (writes into
 * Downloads/VDOGrabber and shows a notification with progress).
 * Direct files (mp4/webm/mp3...) download fully; m3u8/mpd manifests are
 * rejected here - MainActivity explains that streams need the desktop engine.
 */
object Downloader {

    val STREAM_EXT = setOf("m3u8", "mpd")

    fun extOf(url: String): String =
        Regex("""\.(mp4|webm|mkv|m3u8|mpd|flv|mov|avi|mp3|m4a|aac|ts|3gp)([?#].*)?${'$'}""", RegexOption.IGNORE_CASE)
            .find(url)?.groupValues?.get(1)?.lowercase() ?: ""

    fun isStream(url: String): Boolean = extOf(url) in STREAM_EXT

    fun enqueue(ctx: Context, url: String, title: String): Long {
        val name = (title.ifEmpty { url.substringAfterLast('/') }).substringBefore('?')
            .ifEmpty { "video" }
        val fileName = if (name.contains('.')) name else "$name.${extOf(url).ifEmpty { "mp4" }}"

        val req = DownloadManager.Request(Uri.parse(url)).apply {
            setTitle(fileName)
            setDescription("VDO Grabber")
            setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "VDOGrabber/$fileName")
            CookieManager.getInstance().getCookie(url)?.let { addRequestHeader("Cookie", it) }
            addRequestHeader("User-Agent", "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36")
            setAllowedOverMetered(true)
            setAllowedOverRoaming(true)
        }
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val id = dm.enqueue(req)
        FileLog.download("enqueue #$id url=$url -> Downloads/VDOGrabber/$fileName")
        FileLog.event("download_queued", mapOf("id" to id, "url" to url, "file" to fileName))
        return id
    }
}
