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
import java.net.InetSocketAddress
import java.net.ServerSocket
import com.sun.net.httpserver.HttpServer

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
        for (name in assets.list("hls").orEmpty()) {
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

        val server = HttpServer.create(InetSocketAddress(port), 0)
        server.createContext("/") { exchange ->
            val rel = exchange.requestURI.path.trimStart('/').ifEmpty { "index.m3u8" }
            val path = File(root, rel)
            if (path.isFile) {
                val bytes = path.readBytes()
                exchange.responseHeaders.add(
                    "Content-Type",
                    if (rel.endsWith(".m3u8")) "application/vnd.apple.mpegurl" else "video/mp2t",
                )
                exchange.sendResponseHeaders(200, bytes.size.toLong())
                exchange.responseBody.use { it.write(bytes) }
            } else {
                exchange.sendResponseHeaders(404, -1)
            }
        }
        server.start()
        try {
            val outDir = File(instr.targetContext.filesDir, "e2e-out").apply { mkdirs() }
            val url = "http://127.0.0.1:$port/index.m3u8"

            val req = YoutubeDLRequest(url).apply {
                StreamArgs.optionsArgs(outDir).forEach { addOption(it) }
            }
            val result = YoutubeDL.getInstance().execute(req)

            assertEquals("yt-dlp should exit 0", 0L, result.exitCode.toLong())
            val file = StreamArgs.newestFileIn(outDir)
            assertTrue("output file must exist", file != null && file.exists())
            assertTrue(
                "merged mp4 should contain real frames (got ${file!!.length()} bytes)",
                file.length() > 50_000,
            )
            file.delete()
        } finally {
            server.stop(0)
            root.deleteRecursively()
        }
    }
}
