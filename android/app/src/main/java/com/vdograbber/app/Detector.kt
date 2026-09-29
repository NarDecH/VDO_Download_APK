package com.vdograbber.app

import org.json.JSONObject

/**
 * In-page video detection JS - the Android twin of app/core/detector.py.
 * 1) DOM scan (video/source/audio tags, og:video meta, JSON-LD, media links)
 * 2) fetch/XHR hooks for m3u8/mpd/mp4 requests (MAIN world)
 * 3) performance resource-timing entries
 * Results go to the AndroidBridge.reportMedia JSON interface.
 */
object Detector {

    val INJECT_JS: String = """
    (function () {
      if (window.__vgDetect) { window.__vgDetect.scan(); return; }
      const MEDIA_RE = /\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|${'$'})/i;
      const seen = new Set();

      function report(url, kind, label) {
        try {
          if (!url || url.startsWith('javascript:') || url.startsWith('about:')) return;
          const abs = new URL(url, location.href).href;
          if (seen.has(abs)) return;
          if (!MEDIA_RE.test(abs) && !abs.startsWith('blob:')) return;
          seen.add(abs);
          const ext = (abs.match(MEDIA_RE) || [])[1] || (abs.startsWith('blob:') ? 'blob' : 'media');
          AndroidBridge.reportMedia(JSON.stringify({
            url: abs, kind: ext.toLowerCase(), label: label || '',
            page: location.href, title: document.title
          }));
        } catch (e) {}
      }

      function scanDom() {
        try {
          document.querySelectorAll('video, audio').forEach(function (v) {
            const src = v.currentSrc || v.src || (v.querySelector('source') && v.querySelector('source').src);
            if (src) report(src, null, v.tagName.toLowerCase() + ' element ' +
              (v.videoWidth ? (v.videoWidth + 'x' + v.videoHeight) : ''));
          });
          document.querySelectorAll('video source, audio source').forEach(function (s) {
            if (s.src) report(s.src, null, '<source>');
          });
          ['og:video', 'og:video:url', 'og:video:secure_url', 'twitter:player:stream'].forEach(function (p) {
            const m = document.querySelector('meta[property="' + p + '"], meta[name="' + p + '"]');
            if (m && m.content) report(m.content, null, 'meta ' + p);
          });
          document.querySelectorAll('script[type="application/ld+json"]').forEach(function (s) {
            try {
              const data = JSON.parse(s.textContent);
              (Array.isArray(data) ? data : [data]).forEach(function (d) {
                if (d && d['@type'] === 'VideoObject' && d.contentUrl) report(d.contentUrl, null, 'JSON-LD');
              });
            } catch (e) {}
          });
          document.querySelectorAll('a[href]').forEach(function (a) {
            if (MEDIA_RE.test(a.href) && !a.href.startsWith('blob:')) report(a.href, null, 'link');
          });
        } catch (e) {}
      }

      function installHooks() {
        try {
          if (!window.__vgFetchOrig) {
            window.__vgFetchOrig = window.fetch;
            window.fetch = function (input) {
              try {
                const u = (typeof input === 'string') ? input : (input && input.url) || '';
                if (MEDIA_RE.test(u)) report(u, null, 'network (fetch)');
              } catch (e) {}
              return window.__vgFetchOrig.apply(this, arguments);
            };
          }
          if (!window.XMLHttpRequest.prototype.__vgOpen) {
            const orig = window.XMLHttpRequest.prototype.open;
            window.XMLHttpRequest.prototype.__vgOpen = true;
            window.XMLHttpRequest.prototype.open = function (method, u) {
              try { if (MEDIA_RE.test(String(u))) report(u, null, 'network (xhr)'); } catch (e) {}
              return orig.apply(this, arguments);
            };
          }
        } catch (e) {}
      }

      function scanResources() {
        try {
          performance.getEntriesByType('resource').forEach(function (r) {
            if (MEDIA_RE.test(r.name)) report(r.name, null, 'network (timing)');
          });
        } catch (e) {}
      }

      window.__vgDetect = { scan: function () { installHooks(); scanDom(); scanResources(); } };
      window.__vgDetect.scan();
      setInterval(function () { window.__vgDetect.scan(); }, 3000);
    })();
    """.trimIndent()

    /** Normalize one report coming from the page. */
    fun parse(json: String): JSONObject? = try {
        val o = JSONObject(json)
        val url = o.optString("url", "")
        if (url.startsWith("http") || url.startsWith("blob")) o else null
    } catch (e: Exception) {
        null
    }
}
