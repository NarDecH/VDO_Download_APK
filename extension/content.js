/**
 * VDO Grabber — ISOLATED-world content script.
 * DOM scan + floating panel UI (Shadow DOM) + bridge between the MAIN world
 * injector and the service worker. Bytes from the page are streamed to the
 * SW in ordered 4 MB chunks (assembled by the offscreen document).
 *
 * Hardened for the wild: every chrome.* access is guarded (the extension can
 * be reloaded/updated while old content scripts are still alive on open
 * tabs), the UI mount survives XML/odd documents, and clipboard copy falls
 * back to execCommand on insecure origins.
 */
(() => {
  if (window.__vgContent) return;
  window.__vgContent = true;

  const MEDIA_RE = /\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i;
  const items = new Map();          // url -> {url, kind, label, via}
  const streams = new Map();        // assembly id -> {name, kind, mime, total, received, queue}
  let seq = 0;
  let shadow = null;                // null = UI could not mount (detection still runs)
  let excluded = false;             // v1.1.3: this page matches a user exclusion pattern

  // ---- site exclusions (v1.1.3) ------------------------------------------
  // Patterns live in chrome.storage.local under "vgExclusions" (one match
  // pattern per entry: "https://www.facebook.com/*", "*://*.tiktok.com/*" or
  // a bare domain like "facebook.com" which also covers subdomains).
  // An excluded page gets NO panel, NO scanning, NO reporting and NO
  // streaming - the extension is fully invisible there. (The MAIN-world
  // injector still hooks fetch/XHR, but its messages are dropped here.)
  function vgExclusionRe(pattern) {
    let p = String(pattern || "").trim().toLowerCase();
    if (!p) return null;
    if (!/^[a-z*]+:\/\//.test(p)) p = "*://" + p;          // bare "host/path" -> any scheme
    const m = p.match(/^([a-z*]+):\/\/([^\/]*)(.*)$/);
    if (!m) return null;
    const scheme = m[1], host = m[2];
    let path = m[3];
    if (!path || path === "/") path = "/*";
    const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const schemeRe = scheme === "*" ? "https?" : esc(scheme);
    // "*.host.tld" matches subdomains AND the bare host (Chrome patterns
    // only cover subdomains - matching the apex too is friendlier); a bare
    // host covers its subdomains as well
    const hostRe = host.startsWith("*.")
      ? "(?:[^/]+\\.)?" + esc(host.slice(2)).replace(/\\\*/g, "[^/]*")
      : host.includes("*")
        ? esc(host).replace(/\\\*/g, "[^/]*")
        : "(?:[^/]+\\.)?" + esc(host);
    const pathRe = esc(path).replace(/\\\*/g, ".*");
    try { return new RegExp("^" + schemeRe + "://" + hostRe + "(?::\\d+)?" + pathRe + "$"); }
    catch (e) { return null; }
  }
  function vgIsExcluded(patterns, url) {
    const u = String(url || "").toLowerCase();
    return (patterns || []).some((p) => { const re = vgExclusionRe(p); return !!re && re.test(u); });
  }
  function logLocal(m) { try { console.debug("[vg]", m); } catch (e) {} }

  const extAlive = () => {
    try { return typeof chrome !== "undefined" && !!(chrome.runtime && chrome.runtime.id); }
    catch (e) { return false; }
  };

  function report(url, kind, label, via) {
    try {
      if (excluded) return;
      if (!url || !/^(https?|blob)/.test(url)) return;
      let k = kind;
      if (!k || k === "media") {
        if (url.startsWith("blob:")) k = "blob";
        else {
          const ext = (url.match(MEDIA_RE) || [])[1];
          k = ext ? ext.toLowerCase() : "media";
        }
      }
      const key = url.slice(0, 400);
      if (items.has(key)) return;
      items.set(key, { url, kind: k, label: label || "", via: via || "" });
      if (extAlive()) {
        try { chrome.runtime.sendMessage({ type: "vg:media", item: { url, kind: k, label: label || "", via: via || "", page: location.href, title: document.title } }); } catch (e) {}
      }
      updateBadge();
      renderList();
    } catch (e) {}
  }

  function scanDom() {
    try {
      if (excluded) return;
      document.querySelectorAll("video, audio").forEach((v) => {
        const src = v.currentSrc || v.src || (v.querySelector("source") && v.querySelector("source").src);
        if (src) report(src, null, (v.tagName.toLowerCase() + " element") + (v.videoWidth ? ` — ${v.videoWidth}×${v.videoHeight}` : ""), "DOM scan");
      });
      document.querySelectorAll("video source, audio source").forEach((s) => { if (s.src) report(s.src, null, "<source>", "DOM scan"); });
      ["og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"].forEach((p) => {
        const m = document.querySelector(`meta[property="${p}"], meta[name="${p}"]`);
        if (m && m.content) report(m.content, null, "meta " + p, "DOM scan");
      });
      document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
        try {
          const data = JSON.parse(s.textContent);
          (Array.isArray(data) ? data : [data]).forEach((d) => {
            if (d && d["@type"] === "VideoObject" && d.contentUrl) report(d.contentUrl, null, "JSON-LD", "DOM scan");
          });
        } catch (e) {}
      });
      document.querySelectorAll("a[href]").forEach((a) => {
        if (MEDIA_RE.test(a.href)) report(a.href, null, "link", "DOM scan");
      });
      performance.getEntriesByType("resource").forEach((r) => {
        if (MEDIA_RE.test(r.name)) report(r.name, null, "network (timing)", "resource timing");
      });
      scanIframes();
    } catch (e) {}
  }

  // ---- iframe scan ------------------------------------------------------
  // - same-origin / srcdoc frames: reachable -> deep scan their DOM + timing
  // - cross-origin frames (typical embed players): the inside is blocked, so
  //   the embed URL itself is offered - yt-dlp/desktop extracts from those.
  const EMBED_RE = /embed|player|video|watch|stream|play|media|vod|\/v\/|\/e\//i;
  function scanIframes() {
    try {
      document.querySelectorAll("iframe").forEach((f) => {
        const src = f.src || "";
        if (src && !/^about:/i.test(src) && EMBED_RE.test(src)) {
          report(src, "embed", "iframe player", "iframe scan");
        }
        let doc = null, win = null;
        try {
          win = f.contentWindow;
          doc = f.contentDocument || (win && win.document);
        } catch (e) { doc = null; }
        if (doc) {
          try {
            doc.querySelectorAll("video, audio").forEach((v) => {
              const s = v.currentSrc || v.src || (v.querySelector("source") && v.querySelector("source").src);
              if (s) report(s, null, "iframe: video element" + (v.videoWidth ? ` — ${v.videoWidth}×${v.videoHeight}` : ""), "iframe scan");
            });
            doc.querySelectorAll("a[href]").forEach((a) => {
              if (MEDIA_RE.test(a.href)) report(a.href, null, "iframe: link", "iframe scan");
            });
            if (win && win.performance) {
              win.performance.getEntriesByType("resource").forEach((r) => {
                if (MEDIA_RE.test(r.name)) report(r.name, null, "iframe: network", "iframe scan");
              });
            }
          } catch (e) {}
        }
      });
      document.querySelectorAll("embed[src], object[data]").forEach((o) => {
        const u = o.src || o.data;
        if (u && EMBED_RE.test(u)) report(u, "embed", "<" + o.tagName.toLowerCase() + "> player", "iframe scan");
      });
      // pull in what other frames (and the native sniffer) reported, so the
      // top-frame panel shows the whole tab, not just its own frame
      if (extAlive()) {
        try {
          chrome.runtime.sendMessage({ type: "vg:list" }, (resp) => {
            if (chrome.runtime.lastError || !resp || !resp.ok) return;
            let changed = false;
            (resp.items || []).forEach((m) => {
              if (m.url && !items.has(m.url.slice(0, 400))) {
                items.set(m.url.slice(0, 400), { url: m.url, kind: m.kind, label: m.label || "network", via: m.via || "other frame" });
                changed = true;
              }
            });
            if (changed) { updateBadge(); renderList(); }
          });
        } catch (e) {}
      }
    } catch (e) {}
  }

  // ---------------- messages from MAIN world (streaming) -------------------
  window.addEventListener("message", (e) => {
    try {
      if (e.source !== window || !e.data || !e.data.__vg) return;
      const d = e.data;
      // exclusion management must work even ON an excluded page (otherwise
      // there is no way back from a page that got excluded while open)
      const exclMgmt = d.__vg === "ui" && (d.type === "debugAddExclusion" || d.type === "debugRemoveExclusion");
      if (excluded && !exclMgmt) return;

      // debug bridge: page (E2E tests) asks for SW state through the content script
      if (d.__vg === "ui" && (d.type === "debugLogs" || d.type === "debugDownloads" || d.type === "debugSetFlag" ||
                              d.type === "debugAddExclusion" || d.type === "debugRemoveExclusion")) {
        const type = { debugLogs: "vg:logs", debugDownloads: "vg:downloads", debugSetFlag: "vg:setFlag",
                       debugAddExclusion: "vg:exclusion:add", debugRemoveExclusion: "vg:exclusion:remove" }[d.type];
        const out = { type };
        if (d.type === "debugSetFlag" || d.type === "debugAddExclusion" || d.type === "debugRemoveExclusion") out.value = d.value;
        if (d.type === "debugAddExclusion" || d.type === "debugRemoveExclusion") out.pattern = d.value;
        if (extAlive()) {
          chrome.runtime.sendMessage(out, (resp) => {
            window.postMessage({ __vg: "main", type: d.type, reqId: d.reqId, resp: resp || { err: chrome.runtime.lastError && chrome.runtime.lastError.message } }, "*");
          });
        } else {
          window.postMessage({ __vg: "main", type: d.type, reqId: d.reqId, resp: { err: "extension reloaded" } }, "*");
        }
        return;
      }

      if (d.__vg !== "main") return;
      if (d.type === "media") { report(d.url, null, "network", d.via || "network"); return; }

      const st = streams.get(d.id);
      if (!st) return;

      if (d.type === "blobMeta") {
        st.mime = d.mime || st.mime;
        st.total = d.total || 0;
        st.queue = st.queue.then(() => send({ type: "vg:blobBegin", id: d.id, mime: st.mime }));
      } else if (d.type === "blobChunk") {
        st.received += 4 * 1024 * 1024;
        const mb = st.total ? `${Math.min(st.received, st.total) / 1048576 | 0}/${st.total / 1048576 | 0} MB` : `${st.received / 1048576 | 0} MB`;
        setStatus(`⏳ อ่านข้อมูลจากหน้าเว็บ… ${mb}`);
        st.queue = st.queue.then(() => send({ type: "vg:chunk", id: d.id, b64: d.b64 }));
      } else if (d.type === "blobDone") {
        st.queue = st.queue.then(() => send({ type: "vg:blobEnd", id: d.id, name: st.name, mime: st.mime }))
          .then(() => streams.delete(d.id));
      } else if (d.type === "blobError") {
        st.queue = st.queue.then(() => send({ type: "vg:blobFailed", id: d.id, error: d.error }))
          .then(() => streams.delete(d.id));
      }
    } catch (e) {}
  });

  function send(msg) {
    return new Promise((resolve) => {
      if (!extAlive()) { resolve(); return; }
      try {
        chrome.runtime.sendMessage(msg, () => { void chrome.runtime.lastError; resolve(); });
      } catch (e) { resolve(); }
    });
  }

  function askMain(msg) { try { window.postMessage(Object.assign({ __vg: "ui" }, msg), "*"); } catch (e) {} }

  function newStream(name, kind) {
    const id = "d" + (++seq) + "-" + Date.now();
    streams.set(id, { name, kind, mime: kind === "mse" ? "video/mp4" : "", total: 0, received: 0, queue: Promise.resolve() });
    return id;
  }

  // ---------------- UI (Shadow DOM pill + panel) ---------------------------
  const SHADOW_MARKUP = `
  <style>
    .pill{display:flex;align-items:center;gap:7px;background:linear-gradient(90deg,#ff5f6d,#ffc371);color:#fff;
          border:0;border-radius:22px;padding:9px 16px;font:600 13px 'Segoe UI',system-ui,sans-serif;cursor:pointer;
          box-shadow:0 6px 22px rgba(255,95,109,.45)}
    .panel{display:none;position:absolute;bottom:48px;right:0;width:390px;max-height:65vh;overflow:auto;
           background:#0f1729;border:1px solid #2a3a5c;border-radius:14px;box-shadow:0 16px 48px rgba(0,0,0,.55);
           color:#e8ecf4;font:13px/1.5 'Segoe UI',system-ui,sans-serif;padding:12px}
    .panel h4{margin:2px 4px 10px;color:#93a4c3;font-size:12.5px}
    .item{border:1px solid #233152;border-radius:10px;padding:9px;margin-bottom:8px;background:#131c33}
    .item b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#f1f5f9;font-size:12.5px}
    .item small{color:#7d8db1;word-break:break-all;font-size:11px;display:block;margin-top:2px}
    .row{display:flex;gap:6px;margin-top:7px;flex-wrap:wrap;align-items:center}
    .dl{background:#16a34a;color:#fff;border:0;border-radius:7px;padding:5px 12px;font-weight:700;cursor:pointer;font-size:12px}
    .dl:hover{background:#15803d}
    .cp{background:#243354;color:#cdd6e6;border:0;border-radius:7px;padding:5px 10px;cursor:pointer;font-size:11.5px}
    .tag{display:inline-block;background:#1a2547;color:#93a4c3;border-radius:8px;padding:1px 8px;font-size:10.5px;margin-left:4px}
    .empty{color:#5c6b8c;text-align:center;padding:16px 0;font-size:12px}
    .status{color:#ffc371;font-size:11.5px;margin:6px 4px 0;min-height:16px}
    .count{background:rgba(255,255,255,.25);border-radius:9px;padding:0 7px;font-size:11px}
    .exbtn{background:#7f1d1d;color:#fca5a5;border:0;border-radius:7px;padding:2px 9px;
           font:600 10.5px 'Segoe UI',system-ui,sans-serif;cursor:pointer;float:right;margin-top:1px}
    .exbtn:hover{background:#991b1b}
    .exbtn.arm{background:#fca5a5;color:#450a0a}
  </style>
  <div class="panel" id="vg-panel">
    <h4>🎬 วิดีโอที่ตรวจพบในหน้านี้ <button class="exbtn" id="vg-exbtn" title="ไม่ตรวจจับ/ไม่แสดงแผงบนเว็บนี้อีก (ทั้งโดเมน + ซับโดเมน)">🚫 ยกเว้นเว็บนี้</button></h4>
    <div id="vg-list"><div class="empty">ยังไม่พบวิดีโอ — เล่นวิดีโอในหน้าสักครู่</div></div>
    <div class="status" id="vg-status"></div>
  </div>
  <button class="pill" id="vg-pill">▼ วิดีโอ <span class="count" id="vg-count">0</span></button>`;

  let uiHost = null;
  let uiWired = false;

  // (re)build the floating UI; survives SPA DOM wipes and exotic documents.
  // Returns true when the pill is live in the document.
  function ensureUi() {
    try {
      if (excluded) return false;
      if (!document.documentElement) return false;
      if (!uiHost) {
        uiHost = document.createElement("div");
        uiHost.id = "vg-content-host";
        uiHost.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:2147483647";
      }
      if (!uiHost.shadowRoot) {
        shadow = uiHost.attachShadow({ mode: "open" });
        shadow.innerHTML = SHADOW_MARKUP;
        uiWired = false;
      }
      if (!uiHost.isConnected) document.documentElement.appendChild(uiHost);
      if (!uiWired && shadow.getElementById("vg-pill")) {
        shadow.getElementById("vg-pill").addEventListener("click", () => {
          const p = shadow.getElementById("vg-panel");
          p.style.display = p.style.display === "block" ? "none" : "block";
          renderList();
        });
        // v1.1.4: two-step "exclude this site" (no accidental clicks)
        const exb = shadow.getElementById("vg-exbtn");
        if (exb) {
          let armTimer = null;
          exb.addEventListener("click", () => {
            if (exb.classList.contains("arm")) {
              clearTimeout(armTimer);
              exb.classList.remove("arm");
              exb.textContent = "⏳ กำลังยกเว้น…";
              excludeThisSite();
            } else {
              exb.classList.add("arm");
              exb.textContent = "ยืนยัน? แผงจะหายจากเว็บนี้";
              armTimer = setTimeout(() => {
                exb.classList.remove("arm");
                exb.textContent = "🚫 ยกเว้นเว็บนี้";
              }, 4000);
            }
          });
        }
        uiWired = true;
      }
      return uiWired;
    } catch (e) {
      shadow = null;   // exotic document (XML etc.) - keep detection, skip the UI
      return false;
    }
  }

  // v1.1.3: decide exclusion before anything mounts. An excluded page never
  // mounts the UI, never scans, never reports, never streams.
  (async () => {
    try {
      if (extAlive()) {
        const s = await chrome.storage.local.get("vgExclusions");
        if (vgIsExcluded(s.vgExclusions || [], location.href)) {
          excluded = true;
          items.clear();
          if (uiHost && uiHost.isConnected) uiHost.remove();
          logLocal("excluded: " + location.href.slice(0, 100));
          return;
        }
      }
    } catch (e) {}
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => { if (ensureUi()) scanDom(); });
    } else {
      if (ensureUi()) scanDom();
    }
    setInterval(() => { ensureUi(); scanDom(); }, 3000);
  })();

  function updateBadge() {
    if (!shadow) return;
    const c = shadow.getElementById("vg-count");
    if (c) c.textContent = String(items.size);
  }

  function setStatus(t) {
    if (!shadow) return;
    const s = shadow.getElementById("vg-status");
    if (s) s.textContent = t;
  }

  function esc(s) { return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function renderList() {
    if (!shadow) return;
    const list = shadow.getElementById("vg-list");
    if (!list || !items.size) return;
    const kindLabel = { blob: "blob (in-page)", m3u8: "HLS", mpd: "DASH", embed: "iframe embed" };
    list.innerHTML = [...items.values()].reverse().map((m) => `
      <div class="item">
        <b>${esc(m.label || m.kind)}<span class="tag">${esc(kindLabel[m.kind] || m.kind)}</span></b>
        <small>${esc(m.url.slice(0, 140))}</small>
        <div class="row">
          <button class="dl" data-u="${esc(m.url)}" data-k="${esc(m.kind)}">⬇ ดาวน์โหลด</button>
          <button class="cp" data-u="${esc(m.url)}">คัดลอกลิงก์</button>
          ${m.kind === "embed" ? `<button class="cp" data-open="${esc(m.url)}" title="เปิดหน้า embed นี้เป็นหน้าหลัก แล้วสแกนวิดีโอต่อ">🌐 เปิดหน้าเว็บ</button>` : ""}
        </div>
      </div>`).join("");
    list.querySelectorAll(".dl").forEach((b) => b.addEventListener("click", () => startDownload(b.getAttribute("data-u"), b.getAttribute("data-k"))));
    list.querySelectorAll(".cp").forEach((b) => b.addEventListener("click", () => copyText(b.getAttribute("data-u"))));
    list.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => {
      try { window.location.href = b.getAttribute("data-open"); } catch (e) {}
    }));
  }

  // v1.1.4: exclude the CURRENT site (bare domain form = this host + its
  // subdomains, every path) through the same message the popup sends,
  // then make this page invisible immediately.
  function excludeThisSite() {
    try {
      // hostname (NOT host): a bare-domain pattern must survive ports
      // (http://127.0.0.1:8799/ -> "127.0.0.1", default ports are elided anyway)
      const pattern = location.hostname || "";
      if (!pattern) return;
      const done = () => {
        excluded = true;
        items.clear();
        if (uiHost && uiHost.isConnected) uiHost.remove();
        logLocal("excluded from in-page button: " + pattern);
      };
      if (extAlive()) {
        try {
          chrome.runtime.sendMessage({ type: "vg:exclusion:add", pattern }, (resp) => {
            void chrome.runtime.lastError; // pattern still stored? no - SW unreachable
            done();
          });
        } catch (e) { done(); }
      } else {
        done();
      }
    } catch (e) {}
  }

  function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => setStatus("คัดลอกลิงก์แล้ว")).catch(() => legacyCopy(text));
        return;
      }
    } catch (e) {}
    legacyCopy(text);
  }

  function legacyCopy(text) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;left:-9999px;top:0";
      document.documentElement.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      setStatus("คัดลอกลิงก์แล้ว");
    } catch (e) {
      setStatus("คัดลอกไม่ได้ — เว็บนี้บล็อกคลิปบอร์ด");
    }
  }

  // ---------------- download pipeline --------------------------------------
  function pageReadFallback(url, kind, name) {
    if (kind === "blob") {
      const id = newStream(name, "blob");
      setStatus("⏳ อ่าน blob จากหน้าเว็บ…");
      askMain({ type: "readBlob", id, url });
    } else if (kind === "m3u8" || kind === "mpd") {
      setStatus("📺 สตรีม HLS/DASH — ใช้ VDOGrabber เวอร์ชันเดสก์ท็อป (yt-dlp) เพื่อรวมไฟล์");
    } else {
      // http(s) media the browser refused (expired token / picky CDN):
      // re-fetch with the page's own credentials and stream it out
      const id = newStream(name, "media");
      setStatus("⏳ อ่านไฟล์ด้วย fetch ของหน้าเว็บ…");
      askMain({ type: "readUrl", id, url });
    }
  }

  function startDownload(url, kind) {
    if (excluded) return;
    if (!extAlive()) {
      setStatus("⚠️ Extension ถูกอัปเดต — รีเฟรชหน้าเว็บ (F5) แล้วกดใหม่");
      return;
    }
    try { chrome.runtime.sendMessage({ type: "vg:log", msg: `content startDownload kind=${kind} url=${url.slice(0, 80)}` }); } catch (e) {}
    const name = (document.title || "video").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 80) || "video";
    if (kind === "m3u8" || kind === "mpd") {
      setStatus("📺 สตรีม HLS/DASH — ใช้ VDOGrabber เวอร์ชันเดสก์ท็อป (yt-dlp) เพื่อรวมไฟล์");
      return;
    }
    setStatus("⏳ กำลังส่งให้ตัวดาวน์โหลด…");
    chrome.runtime.sendMessage({ type: "vg:download", item: { url, kind, name, page: location.href } }, (resp) => {
      const lastErr = chrome.runtime.lastError;
      if (lastErr || !resp || !resp.ok) {
        pageReadFallback(url, kind, name);
      } else {
        setStatus("✅ เริ่มดาวน์โหลดแล้ว — ดูในแถบดาวน์โหลดของ Chrome");
      }
    });
  }

  // messages from the service worker
  chrome.runtime.onMessage.addListener((msg, sender) => {
    try {
      if (sender.id !== chrome.runtime.id) return;
      // the SW tells us the exclusion list changed - even an excluded page
      // must hear this so removing the pattern brings the panel back live
      if (msg.type === "vg:exclusionsUpdated") {
        // re-evaluate this page right away (the popup just changed the list)
        (async () => {
          try {
            const s = await chrome.storage.local.get("vgExclusions");
            const isEx = vgIsExcluded(s.vgExclusions || [], location.href);
            if (isEx && !excluded) {
              excluded = true;
              items.clear();
              if (uiHost && uiHost.isConnected) uiHost.remove();
            } else if (!isEx && excluded) {
              excluded = false;
              if (ensureUi()) scanDom();
            }
          } catch (e) {}
        })();
        return;
      }
      if (msg.type === "vg:netMedia") report(msg.url, msg.kind, "network", "webRequest");
      else if (msg.type === "vg:blobSaved") setStatus("✅ ดาวน์โหลดสำเร็จ: " + (msg.filename || ""));
      else if (msg.type === "vg:clearMedia") {
        items.clear();
        updateBadge();
        if (shadow) {
          const list = shadow.getElementById("vg-list");
          if (list) list.innerHTML = '<div class="empty">ยังไม่พบวิดีโอ — เล่นวิดีโอในหน้าสักครู่</div>';
        }
      } else if (msg.type === "vg:downloadFailed") {
        // chrome.downloads reported the async failure (MSE blob / expired link)
        const it = msg.item || {};
        setStatus("⚠️ Chrome ดาวน์โหลดตรงไม่สำเร็จ — สลับไปอ่านข้อมูลจากหน้าเว็บ…");
        pageReadFallback(it.url, it.kind, it.name || (document.title || "video").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 80));
      } else if (msg.type === "vg:blobSaveFailed") {
        // last resort: MSE capture (for blob: sources backed by MediaSource)
        const mseStream = [...streams.values()].find((s) => s.kind === "mse");
        if (!mseStream) {
          const name = (document.title || "video").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 80) || "video";
          const id = newStream(name, "mse");
          setStatus("⏳ พยายามรวมชิ้นส่วนสตรีม (MSE capture)…");
          askMain({ type: "readMse", id });
        } else {
          setStatus("❌ " + (msg.error || "ดาวน์โหลดไม่สำเร็จ") + " — ลองเล่นวิดีโอให้ครบแล้วกดใหม่ หรือใช้เวอร์ชันเดสก์ท็อป");
        }
      }
    } catch (e) {}
  });
})();
