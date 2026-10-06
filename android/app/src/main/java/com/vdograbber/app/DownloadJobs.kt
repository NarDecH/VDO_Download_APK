package com.vdograbber.app

import java.util.concurrent.ConcurrentHashMap

/**
 * v1.5.0: registry of the ACTIVE stream downloads (per yt-dlp process id).
 *
 * Pure JVM state so both the media sheet's "กำลังดาวน์โหลด" tab and the chip
 * ticker can list every running job (pid, url, title, percent) and cancel one
 * of them - before this the service only kept a single static lastPercent, so
 * overlapping downloads reported each other's progress and could only be
 * cancelled as a group. Unit-tested on the JVM (DownloadJobsTest); the
 * service (StreamDownloadService) owns the lifecycle around this registry.
 */
object DownloadJobs {

    class Job(val pid: String, val url: String, val title: String) {
        /** yt-dlp percent 0..99, or -1 until the first progress report. */
        @Volatile var percent: Int = -1

        /** Wall clock at start - drives the stable oldest-first tab order. */
        @Volatile var startedAt: Long = 0
    }

    private val jobs = ConcurrentHashMap<String, Job>()

    /** pids whose cancel was user-requested but whose coroutine has not
     *  observed the kill yet - lets the service report "cancelled" instead of
     *  a fake failure ("yt-dlp exit ...") when the process dies on purpose. */
    private val cancelledPids: MutableSet<String> = ConcurrentHashMap.newKeySet()

    /** Register a job (done by the service right before the engine starts). */
    fun register(pid: String, url: String, title: String): Job {
        val j = Job(pid, url, title).apply { startedAt = System.currentTimeMillis() }
        jobs[pid] = j
        return j
    }

    fun update(pid: String, percent: Int) {
        jobs[pid]?.percent = percent
    }

    fun remove(pid: String) {
        jobs.remove(pid)
    }

    /** Forget every job (service destroyed - nothing is running anymore). */
    fun clear() {
        jobs.clear()
        cancelledPids.clear()
    }

    /** Active jobs, oldest first (stable order in the downloading tab). */
    fun active(): List<Job> = jobs.values.sortedBy { it.startedAt }

    fun count(): Int = jobs.size

    fun get(pid: String): Job? = jobs[pid]

    /** -1 when idle, otherwise the highest reported percent (chip ticker). */
    fun currentPercent(): Int = jobs.values.maxOfOrNull { it.percent } ?: -1

    /** Mark [pid] as user-cancelled and drop it from the active list so the
     *  UI updates at once; the worker later consumes the flag. */
    fun markCancelled(pid: String): Boolean {
        if (jobs.remove(pid) == null) return false
        cancelledPids.add(pid)
        return true
    }

    /** Consume the cancelled flag for [pid] (one-shot, for the worker). */
    fun consumeCancelled(pid: String): Boolean = cancelledPids.remove(pid)
}
