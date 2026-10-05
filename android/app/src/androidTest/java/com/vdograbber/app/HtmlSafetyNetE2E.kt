package com.vdograbber.app

import android.app.DownloadManager
import android.content.Context
import android.net.Uri
import android.os.Environment
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * v1.3.5 E2E from the field log (merrylion2.com DASH player):
 *
 *  1. htmlPageDownloadIsDeletedAndDetected - a DownloadManager job whose
 *     payload is an HTML player page (exactly the reported fake .mp4) must
 *     be detected by Downloader.verifyNotHtmlAndClean, the fake file must
 *     be gone, and pendingFor() must hand the URL back for re-routing.
 *
 *  2. dashManifestOnLoopbackDownloadsRealMedia - an MPD with SegmentTemplate
 *     ($Number$) served over loopback goes through the REAL engine with the
 *     SAME args the service uses (app-private work dir) and produces the
 *     concatenated fMP4 - the "output.mpd" scenario.
 *
 *  The DASH fixtures are generated at runtime (ffmpeg is not available on
 *  the emulator): init.mp4 (ftyp+moov) + two moof/mdat fragments + a minimal
 *  SegmentTemplate MPD pointing at them. A single video representation is
 *  appended raw by yt-dlp, so the merged file must be init+seg1+seg2 in order.
 */
@RunWith(AndroidJUnit4::class)
class HtmlSafetyNetE2E {

    private val instr = InstrumentationRegistry.getInstrumentation()
    private val ctx: Context get() = instr.targetContext

    // ------------------------------------------------ 1. HTML safety net

    @Test
    fun htmlPageDownloadIsDeletedAndDetected() {
        val page = """<!DOCTYPE html>
<html lang="en"><head><title>Player</title>
<script src="https://cdn.dashjs.org/latest/dash.all.min.js"></script></head>
<body><video id="videoPlayer" controls autoplay></video>
<script>player.initialize(document.querySelector("#videoPlayer"),
"https://merrylion2.com/ohudo36m3d/output.mpd", true);</script></body></html>"""
        val payload = page.toByteArray(Charsets.UTF_8)

        val port = java.net.ServerSocket(0).use { it.localPort }
        val root = File(ctx.cacheDir, "html-e2e").apply { mkdirs() }
        File(root, "player.html").writeBytes(payload)
        val server = FixtureServer(port, root)
        server.start()
        try {
            val name = "e2e-html-fake.mp4"
            val id = dmEnqueue("http://127.0.0.1:$port/player.html", name)
            assertTrue("download must finish within 60s", waitDm(id))

            val f = File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                "VDOGrabber/$name",
            )
            assertTrue("fake mp4 must exist before the sniff", f.exists())

            // re-route info must be available BEFORE the check (receiver flow)
            Downloader.putPending(id, "http://example/player.html" to "Player")
            assertTrue("HTML payload must be detected", Downloader.verifyNotHtmlAndClean(ctx, id))
            assertFalse("fake file must be deleted", f.exists())
            assertEquals("Player", Downloader.pendingFor(id)?.second)
            Downloader.forget(id)

            // a real mp4 must NOT be flagged: same pipeline, media bytes
            val mp4root = File(ctx.cacheDir, "mp4-e2e").apply { mkdirs() }
            File(mp4root, "clip.mp4").writeBytes(
                byteArrayOf(0, 0, 0, 0x18, 0x66, 0x74, 0x79, 0x70) + ByteArray(4096))
            val port2 = java.net.ServerSocket(0).use { it.localPort }
            val server2 = FixtureServer(port2, mp4root)
            server2.start()
            try {
                val name2 = "e2e-real.mp4"
                val id2 = dmEnqueue("http://127.0.0.1:$port2/clip.mp4", name2)
                assertTrue("real mp4 download must finish", waitDm(id2))
                val f2 = File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
                    "VDOGrabber/$name2",
                )
                Downloader.putPending(id2, "http://example/clip.mp4" to "clip")
                assertFalse("real mp4 must survive the sniff",
                    Downloader.verifyNotHtmlAndClean(ctx, id2))
                assertTrue("real mp4 must still exist", f2.exists())
                f2.delete()
            } finally {
                server2.stop()
                mp4root.deleteRecursively()
            }
        } finally {
            server.stop()
            root.deleteRecursively()
        }
    }

    // --------------------------------- 2. DASH (SegmentTemplate) via the engine

    @Test
    fun dashManifestOnLoopbackDownloadsRealMedia() {
        YoutubeDL.getInstance().init(ctx) // idempotent
        val root = File(ctx.cacheDir, "dash-e2e").apply { mkdirs() }

        val port = java.net.ServerSocket(0).use { it.localPort }
        writeDashFixtures(root, port)
        val server = FixtureServer(port, root)
        server.start()
        try {
            // the service's REAL work dir (v1.3.5)
            val outDir = StreamArgs.engineWorkDir(
                ctx.getExternalFilesDir(null) ?: ctx.filesDir).apply { mkdirs() }
            val url = "http://127.0.0.1:$port/stream.mpd"

            val req = YoutubeDLRequest(url).apply {
                StreamArgs.optionsArgs(outDir).forEach { addOption(it) }
                addOption("-f", "bv*+ba/b")
            }
            val result = YoutubeDL.getInstance().execute(req)
            assertEquals("yt-dlp should exit 0", 0L, result.exitCode.toLong())

            val file = StreamArgs.newestFileIn(outDir)
            assertTrue("engine must produce a file in its work dir", file != null && file!!.exists())
            val bytes = file!!.readBytes()
            // init + seg1 + seg2 concatenated in order (raw append for one rep)
            val expected = File(root, "init.mp4").readBytes() +
                File(root, "seg1.m4s").readBytes() + File(root, "seg2.m4s").readBytes()
            assertTrue("merged file too small (${bytes.size}B)", bytes.size > 1000)
            assertTrue(
                "bytes must be init+seg1+seg2 (got ${bytes.size}B vs ${expected.size}B)",
                bytes.size >= expected.size &&
                    bytes.copyOfRange(0, expected.size).contentEquals(expected),
            )
            file.delete()
            outDir.listFiles()?.forEach { it.delete() }
        } finally {
            server.stop()
            root.deleteRecursively()
        }
    }

    // ---------------------------------------------------------------- helpers

    private fun dmEnqueue(url: String, name: String): Long {
        // NOTE: VISIBILITY_HIDDEN (=2) needs the system ACCESS_DOWNLOAD_MANAGER
        // permission - a third-party app gets SecurityException "Invalid value
        // for visibility: 2"; use the same visibility the production code uses
        val req = DownloadManager.Request(Uri.parse(url))
            .setTitle(name)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalPublicDir(
                Environment.DIRECTORY_DOWNLOADS, "VDOGrabber/$name")
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        return dm.enqueue(req)
    }

    private fun waitDm(id: Long): Boolean {
        val dm = ctx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val deadline = System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(60)
        while (System.currentTimeMillis() < deadline) {
            dm.query(DownloadManager.Query().setFilterById(id))?.use { c ->
                if (c.moveToFirst()) {
                    val st = c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                    if (st == DownloadManager.STATUS_SUCCESSFUL) return true
                    if (st == DownloadManager.STATUS_FAILED) {
                        throw AssertionError("DownloadManager job failed (reason=" +
                            c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON)) + ")")
                    }
                }
            }
            Thread.sleep(500)
        }
        return false
    }

    /** Minimal DASH SegmentTemplate fixture: ftyp+moov init + two moof/mdat parts. */
    private fun writeDashFixtures(root: File, port: Int) {
        fun box(type: String, payload: ByteArray): ByteArray {
            val size = 8 + payload.size
            val b = ByteArray(size)
            b[0] = (size ushr 24).toByte(); b[1] = (size ushr 16).toByte()
            b[2] = (size ushr 8).toByte(); b[3] = size.toByte()
            type.toByteArray(Charsets.US_ASCII).copyInto(b, 4)
            payload.copyInto(b, 8)
            return b
        }
        val ftyp = box("ftyp", "isom".toByteArray(Charsets.US_ASCII) +
            byteArrayOf(0, 0, 2, 0) + "isomiso2avc1mp41".toByteArray(Charsets.US_ASCII))
        val moovPayload = ByteArray(64) // placeholder moov; sizes need not parse
        moovPayload[4] = 'm'.code.toByte(); moovPayload[5] = 'o'.code.toByte()
        moovPayload[6] = 'o'.code.toByte(); moovPayload[7] = 'v'.code.toByte()
        File(root, "init.mp4").writeBytes(ftyp + box("moov", moovPayload))
        val mk: (Int) -> ByteArray = { n ->
            val mdatPayload = ByteArray(2048) { i -> ((i + n) and 0xFF).toByte() }
            val moofPayload = ByteArray(32)
            moofPayload[8] = n.toByte()
            box("moof", moofPayload) + box("mdat", mdatPayload)
        }
        File(root, "seg1.m4s").writeBytes(mk(1))
        File(root, "seg2.m4s").writeBytes(mk(2))
        // SegmentTemplate MPD with $Number$ addressing (like output.mpd);
        // ${'$'} escapes the Kotlin template so yt-dlp sees a literal $Number$
        File(root, "stream.mpd").writeText(
            """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static"
     mediaPresentationDuration="PT4S" minBufferTime="PT1S">
  <Period>
    <AdaptationSet mimeType="video/mp4" segmentAlignment="true">
      <Representation id="v1" bandwidth="500000" width="320" height="240"
                      codecs="avc1.64000c">
        <BaseURL>http://127.0.0.1:$port/</BaseURL>
        <SegmentTemplate timescale="1000" duration="2000"
                         initialization="init.mp4"
                         media="seg${'$'}Number$.m4s" startNumber="1" />
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>""",
        )
    }
}

/**
 * Minimal single-thread HTTP/1.0 server over a plain ServerSocket
 * (com.sun.net.httpserver is not part of Android's runtime) with the
 * content types the DASH/HLS fixtures need.
 */
private class FixtureServer(private val port: Int, private val root: File) {
    @Volatile private var running = false
    private var socket: java.net.ServerSocket? = null
    private var thread: Thread? = null

    fun start() {
        running = true
        socket = java.net.ServerSocket(port)
        thread = Thread {
            while (running) {
                val client = try { socket!!.accept() } catch (e: Exception) { break }
                handle(client)
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun handle(client: java.net.Socket) {
        client.use { c ->
            val reader = c.getInputStream().bufferedReader()
            val requestLine = reader.readLine() ?: return
            var line: String?
            do { line = reader.readLine() } while (!line.isNullOrEmpty()) // drain headers
            val rel = requestLine.split(" ").getOrNull(1)
                ?.trimStart('/')?.ifEmpty { "index.m3u8" }?.substringBefore('?')
                ?: return
            val path = File(root, rel)
            val out = c.getOutputStream()
            if (path.isFile) {
                val bytes = path.readBytes()
                val type = when {
                    rel.endsWith(".m3u8") -> "application/vnd.apple.mpegurl"
                    rel.endsWith(".mpd") -> "application/dash+xml"
                    rel.endsWith(".m4s") -> "video/iso.segment"
                    rel.endsWith(".html") -> "text/html"
                    else -> "video/mp4"
                }
                out.write(("HTTP/1.0 200 OK\r\nContent-Type: $type\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n").toByteArray())
                out.write(bytes)
            } else {
                out.write("HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
            }
            out.flush()
        }
    }

    fun stop() {
        running = false
        try { socket?.close() } catch (_: Exception) {}
        thread?.join(1000)
    }
}
