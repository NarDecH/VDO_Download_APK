package com.vdograbber.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.net.ServerSocket

/**
 * Real-engine E2E for the on-device HLS pipeline (docs/plan-android-hls.md
 * step 5, CI variant).
 *
 * The workflow generates a real HLS stream with ffmpeg into
 * src/androidTest/assets/hls/ (index.m3u8 + segN.ts). This test serves those
 * bytes over loopback HTTP and runs the SAME yt-dlp options that
 * StreamArgs.optionsArgs() produces, asserting a merged mp4 comes back.
 * No network beyond 127.0.0.1, so it is deterministic.
 */
@RunWith(AndroidJUnit4::class)
class StreamEngineTest {

    private val instr = InstrumentationRegistry.getInstrumentation()

    private fun copyFixtureTo(root: File) {
        val assets = instr.context.assets
        val names: Array<String> = assets.list("hls") ?: arrayOf()
        for (name in names) {
            assets.open("hls/$name").use { input ->
                File(root, name).outputStream().use { input.copyTo(it) }
            }
        }
    }

    @Test
    fun engineIsInitialized() {
        YoutubeDL.getInstance().init(instr.targetContext) // idempotent
        val v = YoutubeDL.getInstance().version(instr.targetContext)
        assertTrue("engine version should be non-empty", v.isNotEmpty())
    }

    @Test
    fun downloadsLocalHlsToEndToEndMp4() {
        YoutubeDL.getInstance().init(instr.targetContext)
        val port = ServerSocket(0).use { it.localPort }

        val root = File(instr.targetContext.cacheDir, "hls-e2e").apply { mkdirs() }
        copyFixtureTo(root)
        assertTrue("fixture must be present", File(root, "index.m3u8").exists())

        val server = RawHttpServer(port, root)
        server.start()
        try {
            val outDir = File(instr.targetContext.filesDir, "e2e-out").apply { mkdirs() }
            val url = "http://127.0.0.1:$port/index.m3u8"

            val req = YoutubeDLRequest(url).apply {
                StreamArgs.optionsArgs(outDir).forEach { addOption(it) }
            }
            val result = YoutubeDL.getInstance().execute(req)

            assertEquals("yt-dlp should exit 0", 0L, result.exitCode.toLong())
            val files = outDir.listFiles()?.filter { it.isFile }.orEmpty()
            assertTrue("output directory must contain the merged media", files.isNotEmpty())
            val file = files.maxByOrNull { it.lastModified() }!!
            assertTrue(
                "merged mp4 should contain real frames (got ${file!!.length()} bytes)",
                file.length() > 50_000,
            )
            file.delete()
        } finally {
            server.stop()
            root.deleteRecursively()
        }
    }
}

/**
 * Minimal single-thread HTTP/1.0 server over a plain ServerSocket.
 * (com.sun.net.httpserver is not part of Android's runtime.)
 */
private class RawHttpServer(private val port: Int, private val root: File) {
    @Volatile private var running = false
    private var socket: ServerSocket? = null
    private var thread: Thread? = null

    fun start() {
        running = true
        socket = ServerSocket(port)
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
                val type = if (rel.endsWith(".m3u8")) "application/vnd.apple.mpegurl" else "video/mp2t"
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
