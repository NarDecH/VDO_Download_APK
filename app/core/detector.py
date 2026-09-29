"""In-page video detection, replicating the techniques of the reference
"Video Download Helper" extension (see docs/research.md):

  1. DOM scan        - <video>/<source>/<audio>, og:video meta, JSON-LD VideoObject,
                       <a> links pointing at media files (extension: content scripts)
  2. Network hooks   - wrap window.fetch / XMLHttpRequest so manifest & media
                       requests (m3u8/mpd/mp4...) surface even when no <video>
                       tag exists (extension: *_untrusted.js MAIN-world scripts)
  3. Resource timing - performance entries catch requests made before we hooked
                       (extension: webRequest observer in the service worker)

The script is injected into every page load and re-armed by a watchdog.
Results are reported to Python via window.pywebview.api.report_media(json).
"""

from __future__ import annotations

import json

# Unique globals so re-injection after navigation is idempotent.
DETECT_JS = r"""
(function () {
  if (window.__vgDetect) { window.__vgDetect.scan(); return; }

  const MEDIA_RE = /\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i;
  const found = new Map();          // url -> report object
  let apiReady = false, pending = [];

  function send(report) {
    if (!report || !report.url) return;
    const key = report.url.slice(0, 400);
    const prev = found.get(key);
    if (prev && JSON.stringify(prev) === JSON.stringify(report)) return;
    found.set(key, report);
    const payload = JSON.stringify({ page: location.href, title: document.title, item: report });
    if (apiReady && window.pywebview && window.pywebview.api) {
      try { window.pywebview.api.report_media(payload); } catch (e) { pending.push(payload); }
    } else pending.push(payload);
    try { window.__vgOnMedia && window.__vgOnMedia(report); } catch (e) {}
  }

  function flush() {
    if (!apiReady && window.pywebview && window.pywebview.api) apiReady = true;
    if (!apiReady) return;
    while (pending.length) { const p = pending.shift(); try { window.pywebview.api.report_media(p); } catch (e) { pending.unshift(p); break; } }
  }
  window.addEventListener('pywebviewready', flush);

  function classify(url, kind) {
    let k = kind;
    if (!k || k === 'media') {
      const ext = (url.match(/\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i) || [])[1];
      k = ext ? ext.toLowerCase() : 'media';
    }
    return k;
  }

  function report(url, kind, extra) {
    if (!url || url.startsWith('javascript:') || url.startsWith('about:')) return;
    if (url.startsWith('blob:')) { send(Object.assign({ url: url, kind: 'blob', label: 'MSE stream (in-memory)' }, extra)); return; }
    send(Object.assign({ url: url, kind: classify(url, kind) }, extra || {}));
  }

  // ---- 1. DOM scan ----------------------------------------------------
  function scanDom() {
    try {
      document.querySelectorAll('video, audio').forEach(function (v) {
        const src = v.currentSrc || v.src || (v.querySelector('source') && v.querySelector('source').src);
        if (src) report(src, null, { label: v.tagName.toLowerCase() + ' element', width: v.videoWidth, height: v.videoHeight,
                                     duration: v.duration, poster: v.poster || '', live: !!v.duration });
      });
      document.querySelectorAll('video source, audio source').forEach(function (s) {
        if (s.src) report(s.src, null, { label: '<source>' });
      });
      ['og:video', 'og:video:url', 'og:video:secure_url', 'twitter:player:stream'].forEach(function (p) {
        const m = document.querySelector('meta[property="' + p + '"], meta[name="' + p + '"]');
        if (m && m.content) report(m.content, null, { label: 'meta ' + p });
      });
      document.querySelectorAll('script[type="application/ld+json"]').forEach(function (s) {
        try {
          const data = JSON.parse(s.textContent);
          (Array.isArray(data) ? data : [data]).forEach(function (d) {
            if (d && d['@type'] === 'VideoObject' && d.contentUrl) report(d.contentUrl, null, { label: 'JSON-LD VideoObject' });
          });
        } catch (e) {}
      });
      document.querySelectorAll('a[href]').forEach(function (a) {
        if (MEDIA_RE.test(a.href) && !a.href.startsWith('blob:')) report(a.href, null, { label: 'link: ' + (a.textContent || '').trim().slice(0, 60) });
      });
    } catch (e) {}
  }

  // ---- 2. fetch / XHR hooks (MAIN-world interception) -------------------
  function installHooks() {
    try {
      if (!window.__vgFetchOrig) {
        window.__vgFetchOrig = window.fetch;
        window.fetch = function (input, init) {
          try {
            const u = (typeof input === 'string') ? input : (input && input.url) || '';
            if (MEDIA_RE.test(u) || /\/(manifest|playlist|master|index)\.(m3u8|mpd)/i.test(u)) {
              report(new URL(u, location.href).href, null, { label: 'network (fetch)', via: 'fetch' });
            }
          } catch (e) {}
          return window.__vgFetchOrig.apply(this, arguments);
        };
      }
      if (!window.XMLHttpRequest.prototype.__vgOpen) {
        const orig = window.XMLHttpRequest.prototype.open;
        window.XMLHttpRequest.prototype.__vgOpen = true;
        window.XMLHttpRequest.prototype.open = function (method, u) {
          try {
            if (MEDIA_RE.test(String(u)) || /\/(manifest|playlist|master|index)\.(m3u8|mpd)/i.test(String(u))) {
              report(new URL(u, location.href).href, null, { label: 'network (xhr)', via: 'xhr' });
            }
          } catch (e) {}
          return orig.apply(this, arguments);
        };
      }
    } catch (e) {}
  }

  // ---- 3. performance resource entries (catch pre-hook requests) --------
  function scanResources() {
    try {
      performance.getEntriesByType('resource').forEach(function (r) {
        if (MEDIA_RE.test(r.name)) report(r.name, null, { label: 'network (timing)' });
      });
    } catch (e) {}
  }

  // ---- iframe scan ------------------------------------------------------
  // - same-origin / srcdoc frames: reachable -> deep scan their DOM + timing
  // - cross-origin frames (typical embed players): same-origin policy blocks
  //   the inside, so report the embed URL itself - yt-dlp extracts those.
  const EMBED_RE = /embed|player|video|watch|stream|play|media|vod|\/v\/|\/e\//i;
  function scanIframes() {
    try {
      document.querySelectorAll("iframe").forEach(function (f) {
        const src = f.src || "";
        if (src && !/^about:/i.test(src) && EMBED_RE.test(src)) {
          report(src, "embed", "iframe player (yt-dlp สกัดให้)", "iframe scan");
        }
        let doc = null, win = null;
        try {
          win = f.contentWindow;
          doc = f.contentDocument || (win && win.document);
        } catch (e) { doc = null; }
        if (doc) {
          try {
            doc.querySelectorAll("video, audio").forEach(function (v) {
              const s = v.currentSrc || v.src || (v.querySelector("source") && v.querySelector("source").src);
              if (s) report(s, null, "iframe: video element" + (v.videoWidth ? " — " + v.videoWidth + "×" + v.videoHeight : ""), "iframe scan");
            });
            doc.querySelectorAll("video source, audio source").forEach(function (s) {
              if (s.src) report(s.src, null, "iframe: <source>", "iframe scan");
            });
            doc.querySelectorAll("a[href]").forEach(function (a) {
              if (MEDIA_RE.test(a.href)) report(a.href, null, "iframe: link", "iframe scan");
            });
            if (win && win.performance) {
              win.performance.getEntriesByType("resource").forEach(function (r) {
                if (MEDIA_RE.test(r.name)) report(r.name, null, "iframe: network", "iframe scan");
              });
            }
          } catch (e) {}
        }
      });
      document.querySelectorAll("embed[src], object[data]").forEach(function (o) {
        const u = o.src || o.data;
        if (u && EMBED_RE.test(u)) report(u, "embed", "<" + o.tagName.toLowerCase() + "> player", "iframe scan");
      });
    } catch (e) {}
  }

  // ---- orchestrator -----------------------------------------------------
  let scanCount = 0;
  window.__vgDetect = {
    scan: function (reason) {
      scanCount++;
      installHooks(); scanDom(); scanResources(); scanIframes(); flush();
      // deep scan can be requested from the toolbar button
      return scanCount;
    }
  };

  installHooks();
  window.__vgDetect.scan('initial');
  setInterval(function () { window.__vgDetect.scan('periodic'); }, 3000);
})();
"""

# Floating toolbar + panel UI injected into every page (Shadow DOM isolated).
TOOLBAR_JS = r"""
(function () {
  if (window.__vgToolbar) { window.__vgToolbar.sync(); return; }

  const host = document.createElement('div');
  host.id = 'vg-toolbar-host';
  host.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483646;pointer-events:none;';
  const shadow = host.attachShadow({ mode: 'open' });
  shadow.innerHTML = `
  <style>
    .bar{pointer-events:auto;display:flex;align-items:center;gap:6px;height:44px;padding:0 10px;
         background:linear-gradient(90deg,#0f1729,#16213e);color:#e8ecf4;font:13px/1 'Segoe UI',system-ui,sans-serif;
         box-shadow:0 2px 10px rgba(0,0,0,.35);border-bottom:1px solid #2a3a5c}
    .logo{width:22px;height:22px;border-radius:6px;background:linear-gradient(135deg,#ff5f6d,#ffc371);
          display:flex;align-items:center;justify-content:center;font-size:12px}
    button{all:unset;cursor:pointer;padding:5px 9px;border-radius:7px;font:inherit;color:#cdd6e6}
    button:hover{background:#243354;color:#fff}
    .urlbox{flex:1;display:flex;align-items:center;gap:4px;background:#0b1120;border:1px solid #2a3a5c;border-radius:8px;padding:4px 8px}
    input{all:unset;flex:1;color:#e8ecf4;font:inherit;font-size:12.5px}
    .badge{background:#ef4444;color:#fff;border-radius:9px;padding:1px 7px;font-size:11px;font-weight:700;display:none}
    .btn-dl{background:#16a34a!important;color:#fff!important;font-weight:600}
    .btn-dl:hover{background:#15803d!important}
    .panel{pointer-events:auto;display:none;position:absolute;top:46px;right:10px;width:400px;max-height:60vh;overflow:auto;
           background:#0f1729;border:1px solid #2a3a5c;border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.5);color:#e8ecf4;
           font:13px/1.45 'Segoe UI',system-ui,sans-serif;padding:10px}
    .panel h4{margin:4px 6px 8px;font-size:13px;color:#93a4c3}
    .item{border:1px solid #233152;border-radius:9px;padding:8px;margin-bottom:7px;background:#131c33}
    .item b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#f1f5f9}
    .item small{color:#7d8db1;word-break:break-all}
    .row{display:flex;gap:6px;margin-top:6px;align-items:center;flex-wrap:wrap}
    .dlbtn{background:#16a34a;color:#fff;border-radius:7px;padding:4px 10px;font-weight:600;cursor:pointer;border:0;font-size:12px}
    .fmtbtn{background:#243354;color:#cdd6e6;border-radius:6px;padding:3px 8px;cursor:pointer;border:0;font-size:11.5px}
    .fmtbtn:hover{background:#33477a}
    .fmts{display:none;margin-top:6px}
    .empty{color:#5c6b8c;text-align:center;padding:14px 0}
    .spin{display:inline-block;width:12px;height:12px;border:2px solid #64748b;border-top-color:#e2e8f0;border-radius:50%;
          animation:s .7s linear infinite;vertical-align:-2px;margin-right:5px}
    @keyframes s{to{transform:rotate(360deg)}}
  </style>
  <div class="bar">
    <span class="logo">▼</span>
    <button data-act="back"  title="Back">←</button>
    <button data-act="fwd"   title="Forward">→</button>
    <button data-act="reload" title="Reload">⟳</button>
    <div class="urlbox"><input spellcheck="false" placeholder="ป้อน URL แล้วกด Enter"></div>
    <button data-act="detect" title="Scan this page for videos">🔍 ตรวจหาวิดีโอ</button>
    <button data-act="panel"  title="Videos found">🎬 วิดีโอ <span class="badge">0</span></button>
    <button data-act="ctrl"   title="Downloads / Logs / Settings">⚙️</button>
  </div>
  <div class="panel" id="vgp">
    <h4>🎬 วิดีโอที่ตรวจพบในหน้านี้</h4>
    <div id="vgl"><div class="empty">ยังไม่พบวิดีโอ — กด "ตรวจหาวิดีโอ" หรือเล่นวิดีโอก่อนสักครู่</div></div>
  </div>`;

  document.documentElement.appendChild(host);
  const $ = (s) => shadow.querySelector(s);
  const badge = $('.badge'), panel = $('#vgp'), list = $('#vgl'), urlbox = $('input');
  const pyapi = () => window.pywebview && window.pywebview.api;

  shadow.addEventListener('click', function (e) {
    const b = e.target.closest('button'); if (!b) return;
    const act = b.getAttribute('data-act');
    // navigation stays INSIDE the page on purpose: calling pywebview.api for
    // something that navigates races the return-value callback (the page is
    // gone before Python delivers the result) and throws TypeErrors.
    if (act === 'back') { history.back(); return; }
    if (act === 'fwd') { history.forward(); return; }
    if (act === 'reload') { location.reload(); return; }
    if (act === 'detect') { pyapi() && pyapi().detect_now(); badge.textContent = '...'; return; }
    if (act === 'panel') { panel.style.display = panel.style.display === 'block' ? 'none' : 'block'; return; }
    if (act === 'ctrl') { pyapi() && pyapi().show_control(); return; }
    // open an iframe embed page as the top-level page (in-page navigation)
    if (b.hasAttribute('data-open')) {
      try { location.href = decodeURIComponent(b.getAttribute('data-open')); } catch (e) {}
      return;
    }
    if (b.classList.contains('dlbtn')) {
      pyapi() && pyapi().download_start({ url: decodeURIComponent(b.getAttribute('data-url')),
        kind: classifyKind(b.getAttribute('data-url')), page_url: decodeURIComponent(b.getAttribute('data-ref') || ''), referrer: '' });
      panel.style.display = 'none'; return;
    }
    if (b.classList.contains('fmtbtn')) {
      const box = b.parentElement.parentElement.querySelector('.fmts');
      if (!box) return;
      if (box.dataset.loaded === '1') { box.style.display = box.style.display === 'block' ? 'none' : 'block'; return; }
      box.innerHTML = '<span class="spin"></span> กำลังค้นหารูปแบบ/คุณภาพ...'; box.style.display = 'block';
      const url = decodeURIComponent(b.getAttribute('data-url'));
      pyapi().get_formats(url, '').then(function (r) {
        box.dataset.loaded = '1'; box.innerHTML = '';
        if (!r.ok) { box.innerHTML = '<small style="color:#f87171">ค้นหาไม่สำเร็จ: ' + (r.error || '').slice(0, 120) + '</small>'; return; }
        (r.formats || []).slice(0, 14).forEach(function (f) {
          const btn = document.createElement('button'); btn.className = 'fmtbtn';
          btn.textContent = [f.id, f.ext, f.res, f.tbr ? Math.round(f.tbr) + 'k' : '', f.size ? (f.size / 1048576).toFixed(1) + 'MB' : ''].filter(Boolean).join(' · ');
          btn.onclick = function () { pyapi().download_start({ url: url, format_id: f.id, title: r.title || '', kind: 'media', referrer: '' }); panel.style.display = 'none'; };
          box.appendChild(btn);
        });
        if (!(r.formats || []).length) box.innerHTML = '<small style="color:#fbbf24">ไม่พบรูปแบบรายการ — ใช้ปุ่มดาวน์โหลดอัตโนมัติแทน</small>';
      });
      return;
    }
  });
  function classifyKind(u) { const m = decodeURIComponent(u).match(/\.(m3u8|mpd)(\?|#|$)/i); return m ? m[1].toLowerCase() : 'media'; }
  urlbox.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || !urlbox.value.trim()) return;
    const v = urlbox.value.trim();
    let u;
    if (/^https?:\/\//i.test(v) || /^file:\/\//i.test(v)) u = v;
    else if (/^(localhost|127\.)/i.test(v) || (v.includes('.') && !v.includes(' '))) u = 'https://' + v;
    else u = 'https://duckduckgo.com/?q=' + encodeURIComponent(v);
    location.href = u;   // in-page navigation, no pywebview callback involved
    urlbox.blur();
  });
  document.addEventListener('click', function (e) {
    if (!host.contains(e.target) && e.target !== host && !host.shadowRoot.contains(e.target)) {
      /* allow panel to stay open while interacting with it; close on page clicks */ if (panel.style.display === 'block' && !e.target.closest('#vg-toolbar-host')) panel.style.display = 'none';
    }
  }, true);

  window.__vgToolbar = {
    sync: function () { urlbox.value = location.href; },
    media: function (rep) {
      badge.style.display = 'inline-block';
      badge.textContent = String((window.__vgCount = (window.__vgCount || 0) + 1));
      const empty = list.querySelector('.empty'); if (empty) empty.remove();
      const div = document.createElement('div'); div.className = 'item';
      const label = (rep.label || rep.kind || 'video');
      const q = rep.width && rep.height ? rep.width + '×' + rep.height : '';
      div.innerHTML = '<b>' + label + (q ? ' — ' + q : '') + '</b><small>' + rep.url.slice(0, 160) + '</small>' +
        '<div class="row">' +
        '<button class="dlbtn" data-url="' + encodeURIComponent(rep.url) + '" data-ref="' + encodeURIComponent(location.href) + '">⬇ ดาวน์โหลด</button>' +
        '<button class="fmtbtn" data-url="' + encodeURIComponent(rep.url) + '">รูปแบบ/คุณภาพ</button>' +
        (rep.kind === 'embed' ? '<button class="fmtbtn" data-open="' + encodeURIComponent(rep.url) + '" title="เปิดหน้า embed นี้เป็นหน้าหลัก แล้วสแกนวิดีโอต่อ">🌐 เปิดหน้า embed</button>' : '') +
        '</div>' +
        '<div class="fmts"></div>';
      list.prepend(div);
    }
  };
  window.__vgToolbar.sync();
})();
"""

# Small watchdog: re-arms detection + toolbar if a navigation wiped them.
WATCHDOG_JS = r"""
(function () {
  if (window.__vgWatchdog) return;
  window.__vgWatchdog = true;
  setInterval(function () {
    if (!window.__vgDetect) { try { window.__vgDetect = null; } catch (e) {} }
    if (!window.__vgToolbar) { /* re-inject handled by host app on loaded */ }
  }, 2500);
})();
"""


def build_report(payload_json: str, page_url: str = "") -> dict | None:
    """Validate + normalize a report coming from the page. Returns dict or None."""
    try:
        data = json.loads(payload_json)
        item = data.get("item") or {}
        url = str(item.get("url") or "").strip()
        if not url or not url.startswith(("http", "blob")):
            return None
        return {
            "url": url,
            "kind": str(item.get("kind") or "media"),
            "label": str(item.get("label") or "")[:120],
            "width": item.get("width"), "height": item.get("height"),
            "duration": item.get("duration"),
            "via": str(item.get("via") or item.get("label") or ""),
            "page_url": str(data.get("page") or page_url),
            "page_title": str(data.get("title") or "")[:200],
            "first_seen": None,  # stamped by caller
        }
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None
