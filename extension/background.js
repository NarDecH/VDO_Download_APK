/**
 * VDO Grabber — MV3 service worker.
 *  - webRequest sniffer (observer role of the reference extension's service/main.js)
 *  - download orchestration:
 *      direct http(s) -> blob: URL via downloads API -> page-read bytes streamed
 *      through the offscreen assembler (real Blob + blob: URL, no data-URL size
 *      limit) -> MSE capture. Streaming chunks pass through the SW one by one,
 *      so memory stays at ~1x file size inside the offscreen document.
 *  - per-tab media store (chrome.storage.session) + event log ring buffer
 */
const MAX_ASSEMBLED = 800 * 1024 * 1024; // safety cap for page-read assembly (bytes)
const OFFSCREEN_URL = "offscreen.html";

// ---------------------------------------------------------------- site exclusions (v1.1.3)
// Patterns in chrome.storage.local "vgExclusions" - one Chrome match pattern
// ("https://www.facebook.com/*", "*://*.tiktok.com/*") or a bare domain
// ("facebook.com" = the domain + subdomains, any path). Excluded pages get
// no panel, no detection, no reporting and no streaming.
let vgExclusionsCache = null;
function vgExclusionRe(pattern) {
  let p = String(pattern || "").trim().toLowerCase();
  if (!p) return null;
  if (!/^[a-z*]+:\/\//.test(p)) p = "*://" + p;
  const m = p.match(/^([a-z*]+):\/\/([^\/]*)(.*)$/);
  if (!m) return null;
  const scheme = m[1], host = m[2];
  let path = m[3];
  if (!path || path === "/") path = "/*";
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const schemeRe = scheme === "*" ? "https?" : esc(scheme);
  // "*.host.tld" matches subdomains AND the bare host (Chrome patterns only
  // cover subdomains - matching the apex too is friendlier); a bare host
  // covers its subdomains as well
  const hostRe = host.startsWith("*.")
    ? "(?:[^/]+\\.)?" + esc(host.slice(2)).replace(/\\\*/g, "[^/]*")
    : host.includes("*")
      ? esc(host).replace(/\\\*/g, "[^/]*")
      : "(?:[^/]+\\.)?" + esc(host);
  const pathRe = esc(path).replace(/\\\*/g, ".*");
  try { return new RegExp("^" + schemeRe + "://" + hostRe + "(?::\\d+)?" + pathRe + "$"); }
  catch (e) { return null; }
}
function vgIsExcludedUrl(patterns, url) {
  const u = String(url || "").toLowerCase();
  return (patterns || []).some((p) => { const re = vgExclusionRe(p); return !!re && re.test(u); });
}
async function vgGetExclusions() {
  if (vgExclusionsCache) return vgExclusionsCache;
  try {
    const s = await chrome.storage.local.get("vgExclusions");
    vgExclusionsCache = s.vgExclusions || [];
  } catch (e) { vgExclusionsCache = []; }
  return vgExclusionsCache;
}

// ---------------------------------------------------------------- storage/log
async function tabKey(tabId) { return "tab:" + tabId; }

async function addMedia(tabId, item) {
  if (!tabId || tabId < 0) return;
  // v1.1.3: drop candidates from excluded sites (belt-and-suspenders - the
  // content script already refuses to report from an excluded page)
  if (await vgIsExcludedUrl(await vgGetExclusions(), item && item.page)) return;
  const key = await tabKey(tabId);
  const store = await chrome.storage.session.get(key);
  const list = store[key] || [];
  const dup = list.findIndex((m) => m.url === item.url);
  if (dup >= 0) {
    // v1.1.8: same media seen twice (content scan + webRequest sniffer) -
    // the sniffer knows the exact frame document that loaded the bytes, so
    // its page attribution wins; the embed resolver matches against it.
    if (item.via === "webRequest" && item.page) list[dup].page = item.page;
    return;
  }
  list.push({ ...item, ts: Date.now() });
  await chrome.storage.session.set({ [key]: list.slice(-60) });
  log("media", `${item.kind} ${item.url.slice(0, 110)} via ${item.via || item.label || "?"}`);
  chrome.tabs.sendMessage(tabId, { type: "vg:netMedia", url: item.url, kind: item.kind }).catch(() => {});
}

const logs = [];
let logPersistTimer = null;
function log(event, message) {
  logs.push(`${new Date().toISOString()} [${event}] ${message}`);
  if (logs.length > 400) logs.splice(0, logs.length - 400);
  // persist (best effort) so the popup / tests can read them even when the SW restarts
  clearTimeout(logPersistTimer);
  logPersistTimer = setTimeout(() => {
    chrome.storage.session.set({ vgLogs: logs.slice(-120) }).catch(() => {});
  }, 50);
}
chrome.runtime.onInstalled.addListener(() => log("life", "extension installed"));
chrome.runtime.onStartup.addListener(() => log("life", "browser startup"));

// ---------------------------------------------------------------- offscreen
let offscreenReady = false;
async function ensureOffscreen() {
  const has = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [chrome.runtime.getURL(OFFSCREEN_URL)],
  });
  if (has.length && offscreenReady) return true;
  if (!has.length) {
    try {
      await chrome.offscreen.createDocument({
        url: OFFSCREEN_URL,
        reasons: ["BLOBS"],
        justification: "Assemble downloaded video bytes into a Blob for saving",
      });
      log("offscreen", "created");
    } catch (e) {
      if (!/single offscreen|already exists/i.test(String(e.message))) {
        log("error", "offscreen create: " + e.message);
        return false;
      }
    }
  }
  // wait until the offscreen document actually answers (it loads async)
  for (let i = 0; i < 25; i++) {
    const pong = await offscreen({ type: "asb:ping" });
    if (pong && pong.ok) {
      offscreenReady = true;
      return true;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  log("error", "offscreen never became ready");
  return false;
}

function offscreen(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (r) => {
      if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
      else resolve(r || { ok: false, error: "no response from offscreen" });
    });
  });
}

// ---------------------------------------------------------------- sniffer
const CONTENT_TYPES = /(video\/|audio\/|mpegurl|dash\+xml|octet-stream)/i;
// URL extensions that are actual media files (an iframe/page candidate may
// point at an HTML player instead - saving that yields a useless .html)
const MEDIA_URL_RE = /\.(mp4|webm|mkv|m3u8|mpd|flv|mov|avi|mp3|m4a|aac|ts|3gp)([?#].*)?$/i;
// v1.1.8 (mirrors the desktop app's _page_fallback in app/core/downloader.py):
// when a player page hides its stream inside JS/attributes, fetch the page
// HTML and look for a media URL - absolute, relative or inside atob("...")
// blobs that obfuscated players use.
const HTML_MEDIA_RE = /[^\s"'<>\\]+?\.(?:mp4|webm|m3u8|mpd|mkv|mov|avi|flv|ts|3gp)(?:\?[^\s"'<>\\]*)?/i;
const ATOB_BLOB_RE = /atob\(\s*["']([A-Za-z0-9+/=]{24,})["']\s*\)/g;
function kindOfUrl(u) {
  return /\.m3u8(\?|#|$)/i.test(u) ? "m3u8" : /\.mpd(\?|#|$)/i.test(u) ? "mpd" : "media";
}
function absMediaUrl(u, base) {
  try { return new URL(u.startsWith("//") ? "https:" + u : u, base).href; }
  catch (e) { return ""; }
}
async function scanPlayerHtml(pageUrl) {
  try {
    const r = await fetch(pageUrl, { credentials: "include" });
    if (!r.ok) return null;
    const html = await r.text();
    const grab = (text) => {
      const m = HTML_MEDIA_RE.exec(text);
      return m ? absMediaUrl(m[0], pageUrl) : "";
    };
    const direct = grab(html);
    if (direct) return { url: direct, kind: kindOfUrl(direct) };
    for (const mm of html.matchAll(ATOB_BLOB_RE)) {
      try {
        const dec = atob(mm[1]);
        const hit = grab(dec);
        if (hit) return { url: hit, kind: kindOfUrl(hit) };
      } catch (e) {}
    }
  } catch (e) {}
  return null;
}
chrome.webRequest.onHeadersReceived.addListener(
  async (details) => {
    try {
      if (details.tabId < 0) return;
      // v1.1.3: the page (initiator) is excluded -> never sniff its media
      if (await vgIsExcludedUrl(await vgGetExclusions(), details.initiator || details.originUrl || details.documentUrl)) return;
      const ct = (details.responseHeaders || []).find((h) => h.name.toLowerCase() === "content-type");
      const val = (ct && ct.value) || "";
      if (!CONTENT_TYPES.test(val) || /text\/html/i.test(val)) return;
      const u = details.url;
      if (!/\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i.test(u) && !/mpegurl|dash\+xml/i.test(val)) return;
      const kind = /mpegurl/i.test(val) || /\.m3u8/i.test(u) ? "m3u8" : /dash/i.test(val) || /\.mpd/i.test(u) ? "mpd" : "media";
      // v1.1.8: remember WHICH page loaded this media (documentUrl = the
      // frame document that displayed it). The resolver matches embed/page
      // candidates against this field - the real stream a cross-origin
      // player loads is now attributable to the player, not lost in the
      // tab-wide pile where the old catch-all grabbed the wrong file.
      const page = details.documentUrl || details.initiator || details.originUrl || "";
      addMedia(details.tabId, { url: u, kind, label: "network request", via: "webRequest", page });
    } catch (e) {}
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// tell a tab's content script to re-check the exclusion list right away
// (called after the popup adds a pattern for the current site)
async function vgApplyExclusionToTab(tabId) {
  try { await chrome.tabs.sendMessage(tabId, { type: "vg:exclusionsUpdated" }); } catch (e) {}
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const key = await tabKey(tabId);
  chrome.storage.session.remove(key);
});

// a new navigation invalidates every blob: URL the old document created -
// clear the tab's media list so the panel never offers dead links
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== "loading") return;
  const key = await tabKey(tabId);
  const store = await chrome.storage.session.get(key);
  if (store[key] && store[key].length) {
    await chrome.storage.session.set({ [key]: [] });
    log("nav", "cleared media list for new navigation");
    chrome.tabs.sendMessage(tabId, { type: "vg:clearMedia" }).catch(() => {});
  }
});

// ---------------------------------------------------------------- naming
function safeName(title, ext) {
  const base = (title || "video").replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 80) || "video";
  return `${base}.${ext}`;
}
function extOf(url, mime) {
  const m = (url.match(/\.(mp4|webm|mkv|m3u8|mpd|flv|mov|avi|mp3|m4a|aac|ts|3gp)([?#].*)?$/i) || [])[1];
  if (m) return m.toLowerCase();
  if (mime) {
    const mm = /(?:video|audio)\/(\w+)/i.exec(mime);
    if (mm) return mm[1].toLowerCase();
  }
  return "mp4";
}

// ---------------------------------------------------------------- download
async function downloadUrl(url, filename) {
  // NOTE: chrome.downloads.download rejects restricted headers such as
  // Referer ("Unsafe request header name") - it would kill the whole
  // download. Cookies ride along automatically via the browser profile;
  // hotlink-protected URLs are handled by the page-fetch fallback instead.
  return new Promise((resolve) => {
    try {
      chrome.downloads.download({ url, filename, conflictAction: "uniquify", saveAs: false }, (id) => {
        if (chrome.runtime.lastError || !id) {
          resolve({ ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || "no download id" });
        } else {
          log("download", `started #${id} ${filename}`);
          resolve({ ok: true, id });
        }
      });
    } catch (e) {
      resolve({ ok: false, error: String(e.message || e) });
    }
  });
}

// watch a download started via chrome.downloads:
//  - on complete: release the offscreen blob URL (if any)
//  - on interrupted ("Failed - Network error", typical for MediaSource-backed
//    blob: URLs or expired signed links): hand the job to the page-read chain
function watchDownload(downloadId, tabId, item, blobUrl) {
  const onChange = async (delta) => {
    if (delta.id !== downloadId) return;
    const state = delta.state && delta.state.current;
    if (!state) return;
    chrome.downloads.onChanged.removeListener(onChange);
    if (blobUrl) {
      await ensureOffscreen();
      await offscreen({ type: "asb:revoke", blobUrl });
    }
    if (state === "complete") {
      // v1.1.6 safety net: if a direct download actually saved an HTML page
      // (expired link / mis-detected candidate), Chrome records the server's
      // content-type - delete that useless file and tell the user instead of
      // leaving a fake "video". (MV3 cannot read file: contents, but the
      // downloads API exposes the mime type.)
      try {
        chrome.downloads.search({ id: downloadId }, (d) => {
          const it0 = d && d[0];
          if (!it0) return;
          const file = it0.filename || "";
          if (!/text\/html|application\/xhtml/i.test(it0.mime || "")) return;
          chrome.downloads.removeFile(downloadId, () => {});
          chrome.downloads.erase({ id: downloadId }, () => {});
          log("download", `#${downloadId} saved HTML not a video - deleted ${file.split(/[\/]/).pop()}`);
          if (tabId) chrome.tabs.sendMessage(tabId, {
            type: "vg:downloadFailed",
            item,
            error: "ไม่ใช่ไฟล์วิดีโอ (หน้าเว็บ) — เล่นวิดีโอก่อนแล้วกดใหม่ หรือใช้แอปเดสก์ท็อป",
          }).catch(() => {});
        });
      } catch (e) {}
      log("download", `#${downloadId} complete`);
      return;
    }
    const err = (delta.error && delta.error.current) || "interrupted";
    if (blobUrl) {
      // assembled download failed - nothing more we can do for it here
      log("download", `#${downloadId} ${err} (assembled)`);
      return;
    }
    log("download-fallback", `#${downloadId} ${err} → page read`);
    if (tabId) {
      chrome.tabs.sendMessage(tabId, { type: "vg:downloadFailed", item }).catch(() => {});
    }
  };
  chrome.downloads.onChanged.addListener(onChange);
}

// assemble a stream of base64 chunks (already forwarded to offscreen) into a
// real blob: URL and download it - avoids the data:URL "Failed - Network error"
async function assembleAndDownload(id, mime, filename, tabId) {
  await ensureOffscreen();
  const end = await offscreen({ type: "asb:end", id });
  if (!end.ok) return { ok: false, error: end.error };
  if (end.size > MAX_ASSEMBLED) {
    await offscreen({ type: "asb:revoke", blobUrl: end.blobUrl });
    return { ok: false, error: `ไฟล์ใหญ่ ${(end.size / 1048576).toFixed(0)} MB เกินขีดจำกัดของส่วนขยาย — ใช้เวอร์ชันเดสก์ท็อปแทน` };
  }
  const r = await downloadUrl(end.blobUrl, filename);
  if (!r.ok) {
    await offscreen({ type: "asb:revoke", blobUrl: end.blobUrl });
    return { ok: false, error: r.error };
  }
  watchDownload(r.id, tabId, { url: "", kind: "assembled" }, end.blobUrl);
  if (tabId) chrome.tabs.sendMessage(tabId, { type: "vg:blobSaved", filename }).catch(() => {});
  return { ok: true, filename };
}

// ---------------------------------------------------------------- HLS (v1.1.9)
// Chrome cannot remux HLS - the extension used to refuse m3u8 with "use the
// desktop app". But the SW has host_permissions <all_urls>, so it can fetch
// the manifest + every segment itself (cross-origin, no CORS wall) and
// assemble one .ts file through the offscreen assembler - the same pipeline
// yt-dlp runs for the desktop app.
const MAX_HLS_SEGS = 5000;
const M3U8_ATTR_URL_RE = /(?:URI|URL)\s*=\s*"([^"]+)"/i;

function b64(bytes) {
  let bin = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  return btoa(bin);
}
function resolveUrl(u, base) {
  try { return new URL(u, base).href; } catch (e) { return ""; }
}

async function collectPlaylist(url, depth = 0) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  const text = await r.text();
  if (!/#EXTM3U/i.test(text)) throw new Error("not an M3U8 playlist");
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  // master playlist: take the highest-bandwidth variant and recurse
  if (lines.some((l) => /^#EXT-X-STREAM-INF/i.test(l))) {
    if (depth > 2) throw new Error("master playlist nesting too deep");
    let best = null, bestBw = -1;
    for (let i = 0; i < lines.length - 1; i++) {
      if (!/^#EXT-X-STREAM-INF/i.test(lines[i])) continue;
      const bw = parseInt((lines[i].match(/BANDWIDTH=(\d+)/i) || [])[1] || "0", 10);
      const next = lines[i + 1].startsWith("#") ? null : lines[i + 1];
      if (next && bw > bestBw) { bestBw = bw; best = next; }
    }
    if (!best) throw new Error("master playlist has no variant");
    return collectPlaylist(resolveUrl(best, url), depth + 1);
  }
  const segs = [];
  for (const l of lines) {
    if (/^#EXT-X-KEY/i.test(l) && !/METHOD=NONE/i.test(l)) {
      throw new Error("encrypted HLS (AES-128) - use the desktop app (yt-dlp)");
    }
    if (l.startsWith("#")) {
      // #EXT-X-MAP (fMP4 init segment) must precede the media segments
      if (/^#EXT-X-MAP/i.test(l)) {
        const mm = M3U8_ATTR_URL_RE.exec(l);
        if (mm) segs.push(resolveUrl(mm[1], url));
      }
      continue;
    }
    segs.push(resolveUrl(l, url));
    if (segs.length > MAX_HLS_SEGS) throw new Error("too many segments (live stream?)");
  }
  if (!segs.length) throw new Error("media playlist has no segments");
  return segs;
}

async function downloadHls(manifestUrl, filename, tabId) {
  const id = "hls-" + Date.now() + "-" + Math.floor(Math.random() * 1e6);
  try {
    var segs = await collectPlaylist(manifestUrl);
  } catch (e) {
    return { ok: false, error: "HLS manifest: " + (e.message || e) };
  }
  log("hls", `${segs.length} segments from ${manifestUrl.slice(0, 100)}`);
  await ensureOffscreen();
  const begin = await offscreen({ type: "asb:begin", id, mime: "video/mp2t" });
  if (!begin || !begin.ok) return { ok: false, error: (begin && begin.error) || "assembler unavailable" };
  try {
    for (let i = 0; i < segs.length; i++) {
      const r = await fetch(segs[i], { credentials: "include" });
      if (!r.ok) throw new Error("HTTP " + r.status + " seg " + (i + 1));
      const cr = await offscreen({ type: "asb:chunk", id, b64: b64(new Uint8Array(await r.arrayBuffer())) });
      if (!cr || !cr.ok) throw new Error((cr && cr.error) || "assembler chunk failed");
      if (tabId) chrome.tabs.sendMessage(tabId, { type: "vg:hlsProgress", seg: i + 1, total: segs.length }).catch(() => {});
    }
  } catch (e) {
    await offscreen({ type: "asb:abort", id });
    return { ok: false, error: "HLS segment failed: " + (e.message || e) };
  }
  const fname = String(filename || "video").replace(/\.(m3u8|mp4|ts)?$/i, ".ts");
  const r = await assembleAndDownload(id, "video/mp2t", fname, tabId);
  return r.ok ? { ok: true, hls: true, filename: r.filename } : { ok: false, hls: true, error: r.error };
}

// ---------------------------------------------------------------- messaging
async function forceFallback() {
  const s = await chrome.storage.session.get("forceFallback");
  return !!s.forceFallback; // E2E test hook
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const tabId = sender.tab ? sender.tab.id : null;
    try {
      switch (msg.type) {
      case "vg:media":
        await addMedia(tabId, msg.item);
        sendResponse({ ok: true });
        break;

      // ---- site exclusions (v1.1.3) ----------------------------------
      case "vg:exclusions":
        sendResponse({ ok: true, patterns: await vgGetExclusions() });
        break;

      case "vg:exclusion:add": {
        const pat = String(msg.pattern || "").trim();
        if (!pat) { sendResponse({ ok: false, error: "empty pattern" }); break; }
        const list = await vgGetExclusions();
        if (!list.includes(pat)) list.push(pat);
        vgExclusionsCache = list;
        await chrome.storage.local.set({ vgExclusions: list });
        log("exclusions", `added ${pat} (${list.length} total)`);
        if (tabId != null) await vgApplyExclusionToTab(tabId);
        sendResponse({ ok: true, patterns: list });
        break;
      }

      case "vg:exclusion:remove": {
        const pat2 = String(msg.pattern || "").trim();
        const list2 = (await vgGetExclusions()).filter((x) => x !== pat2);
        vgExclusionsCache = list2;
        await chrome.storage.local.set({ vgExclusions: list2 });
        log("exclusions", `removed ${pat2} (${list2.length} total)`);
        // every tab re-checks itself - an excluded page must learn it is
        // un-excluded even though its content script ignores other traffic
        for (const t of await chrome.tabs.query({})) {
          chrome.tabs.sendMessage(t.id, { type: "vg:exclusionsUpdated" }).catch(() => {});
        }
        sendResponse({ ok: true, patterns: list2 });
        break;
      }

      // ---- exclusion import/export (v1.2.1) - shares the JSON file format
      // with the desktop app (Api.exclusion_export / Api.exclusion_import)
      // so a vdograbber-exclusions.json from one side imports on the other
      case "vg:exclusion:export": {
        const exlist = await vgGetExclusions();
        const doc = {
          app: "VDO Grabber",
          kind: "exclusions",
          version: 1,
          exported: new Date().toISOString(),
          patterns: exlist,
        };
        log("exclusions", `exported ${exlist.length} patterns`);
        sendResponse({ ok: true, json: JSON.stringify(doc, null, 2), count: exlist.length });
        break;
      }

      case "vg:exclusion:import": {
        let doc2 = null;
        try { doc2 = JSON.parse(String(msg.json || "")); } catch (e) { doc2 = null; }
        if (!doc2 || !Array.isArray(doc2.patterns)) {
          sendResponse({ ok: false, error: "no patterns array" });
          break;
        }
        const list3 = await vgGetExclusions();
        let added3 = 0;
        for (const raw of doc2.patterns) {
          const pat3 = String(raw || "").trim().toLowerCase();
          // whitespace inside a pattern is always a typo - drop it
          if (pat3 && !/\s/.test(pat3) && !list3.includes(pat3)) { list3.push(pat3); added3++; }
        }
        vgExclusionsCache = list3;
        await chrome.storage.local.set({ vgExclusions: list3 });
        log("exclusions", `imported +${added3} (${list3.length} total)`);
        // same broadcast as remove: every open tab re-checks itself
        for (const t of await chrome.tabs.query({})) {
          chrome.tabs.sendMessage(t.id, { type: "vg:exclusionsUpdated" }).catch(() => {});
        }
        sendResponse({ ok: true, added: added3, total: list3.length, patterns: list3 });
        break;
      }

      case "vg:download": {
        const item = msg.item || {};
        // popup sends have no sender.tab - the popup passes the active tab id
        const dlTabId = tabId != null ? tabId : (msg.tabId != null ? msg.tabId : null);
        let workUrl = String(item.url || "");
        let workKind = String(item.kind || "");
        log("download-request", `${workKind} ${workUrl.slice(0, 110)}`);

        // v1.1.6: embed/page candidates point at an HTML player page - the
        // old flow downloaded that page and saved a useless .html file. The
        // SW knows what media each page plays (per-tab store, every item
        // carries the page URL that reported it), so resolve there first,
        // then ask the live content scripts as a fallback.
        const isPlayerPage = workKind === "embed" || workKind === "page" ||
                             (/^https?:/i.test(workUrl) && !MEDIA_URL_RE.test(workUrl));
        const pickable = (u) => u && /^(https?:|blob:)/i.test(u) && !/\.html?(\?|#|$)/i.test(u);
        let resolved = false;
        if (isPlayerPage && dlTabId != null) {
          const want = workUrl.replace(/#.*$/, "");
          const key = await tabKey(dlTabId);
          const store = await chrome.storage.session.get(key);
          const tabItems = store[key] || [];
          const want2 = want.replace(/\/$/, "");
          // v1.1.8 strict resolve: ONLY media whose exact URL matches, or
          // media the player page itself loaded (item.page == the player
          // URL). The old third catch-all (`any pickable media in the tab`)
          // downloaded ads / unrelated clips when a cross-origin iframe
          // player was involved - the "wrong file" bug.
          const hit =
            tabItems.find((m) => m.url === want && pickable(m.url) && m.kind !== "embed" && m.kind !== "page") ||
            tabItems.find((m) => pickable(m.url) && (m.page || "").replace(/\/$/, "") === want2);
          if (hit) {
            log("download-resolved", `${workKind} -> ${hit.kind} ${hit.url.slice(0, 110)}`);
            workUrl = hit.url;
            workKind = hit.kind || "media";
            resolved = true;
          } else {
            // last resort: ask the tab's content scripts what is playing
            // (they answer only when they actually find something, so a
            // tab-wide broadcast cannot be flaky)
            try {
              const res = await new Promise((resolve) => {
                let done = false;
                chrome.tabs.sendMessage(dlTabId, { type: "vg:resolveMedia", url: want }, (r) => {
                  done = true; resolve(chrome.runtime.lastError ? null : r);
                });
                setTimeout(() => { if (!done) resolve(null); }, 2500);
              });
              if (res && res.ok && res.url && res.url !== workUrl) {
                log("download-resolved", `${workKind} -> ${res.kind} ${res.url.slice(0, 110)} (live query)`);
                workUrl = res.url;
                workKind = res.kind || "media";
                resolved = true;
              }
            } catch (e) { log("resolve", "tab did not answer: " + e.message); }
          }
        }
        // v1.1.8: nothing in the tab's store and the live content script came
        // up empty - mirror the desktop app's last resort (downloader.py
        // _page_fallback) and scan the player page HTML itself for a media
        // URL (direct, relative, or atob-obfuscated).
        if (isPlayerPage && !resolved && /^https?:/i.test(workUrl) && !MEDIA_URL_RE.test(workUrl)) {
          const found = await scanPlayerHtml(workUrl);
          if (found && found.url && found.url !== workUrl) {
            log("download-resolved", `${workKind} -> ${found.kind} ${found.url.slice(0, 110)} (html scan)`);
            workUrl = found.url;
            workKind = found.kind;
            resolved = true;
          }
        }
        // still a plain http(s) page with no media extension and no in-page
        // match? refuse - never save HTML disguised as a video
        if (isPlayerPage && !resolved && /^https?:/i.test(workUrl) && !MEDIA_URL_RE.test(workUrl)) {
          log("download-refused", `player page without in-page media: ${workUrl.slice(0, 110)}`);
          sendResponse({ ok: false, page: true, error: "no video found on that page - play the video first, or use the desktop app" });
          break;
        }
        // resolved to an HLS/DASH manifest? m3u8 is now downloaded IN the
        // extension (manifest + segments fetched by the SW, assembled into
        // one .ts); DASH (mpd) still needs yt-dlp on the desktop.
        if (resolved && (workKind === "m3u8" || workKind === "mpd")) {
          if (workKind === "m3u8") {
            const hr = await downloadHls(workUrl, safeName(item.name, "ts"), dlTabId);
            log(hr.ok ? "download" : "download-hls-failed", `hls ${workUrl.slice(0, 90)}: ${hr.ok ? hr.filename : hr.error}`);
            sendResponse(hr.ok ? hr : { ok: false, hls: true, error: hr.error });
            return;
          }
          log("download-hls", `resolved to a ${workKind} stream - desktop app required`);
          sendResponse({ ok: false, hls: true, error: "HLS/DASH stream - use the VDOGrabber desktop app (yt-dlp)" });
          break;
        }
        // a DIRECT m3u8/mpd item (no resolution needed) - same routing
        if (/^https?:/i.test(workUrl) && (workKind === "m3u8" || workKind === "mpd")) {
          if (workKind === "m3u8") {
            const hr = await downloadHls(workUrl, safeName(item.name, "ts"), dlTabId);
            log(hr.ok ? "download" : "download-hls-failed", `hls direct ${workUrl.slice(0, 90)}: ${hr.ok ? hr.filename : hr.error}`);
            sendResponse(hr.ok ? hr : { ok: false, hls: true, error: hr.error });
            return;
          }
          sendResponse({ ok: false, hls: true, error: "DASH stream - use the VDOGrabber desktop app (yt-dlp)" });
          break;
        }

        const dlItem = { ...item, url: workUrl, kind: workKind };
        const ext = extOf(workUrl, "");
        const filename = safeName(item.name, workKind === "m3u8" ? "m3u8" : ext);
        let skip = false;
        try { skip = await forceFallback(); } catch (e) { log("error", "forceFallback: " + e.message); }
        // 1) direct http(s) download (cookies ride along with the browser profile)
        if (/^https?:/i.test(workUrl) && !skip) {
          const r = await downloadUrl(workUrl, filename);
          log(r.ok ? "download" : "download-fallback", `direct ${workKind}: ${r.ok ? "ok #" + r.id : r.error}`);
          if (r.ok) {
            watchDownload(r.id, dlTabId, dlItem);
            sendResponse(resolved ? { ok: true, resolved: workUrl } : { ok: true });
            return;
          }
        } else if (workUrl.startsWith("blob:") && !skip) {
          // 2) page-created blob URL: let the downloads system resolve it
          //    while the page is alive (works for real Blob/File objects;
          //    MediaSource-backed blobs fail here and fall through)
          const r = await downloadUrl(workUrl, filename);
          log(r.ok ? "download" : "download-fallback", `blob direct: ${r.ok ? "ok #" + r.id : r.error}`);
          if (r.ok) {
            watchDownload(r.id, dlTabId, dlItem);
            sendResponse({ ok: true });
            return;
          }
        }
        // 3) content script reads bytes inside the page (blob fetch / URL fetch / MSE)
        sendResponse({ ok: false, error: "direct download unavailable" });
        break;
      }

      // ---- streaming assembly (content script forwards page bytes) ----
      case "vg:blobBegin": {
        await ensureOffscreen();
        sendResponse(await offscreen({ type: "asb:begin", id: msg.id, mime: msg.mime || "video/mp4" }));
        break;
      }
      case "vg:chunk": {
        sendResponse(await offscreen({ type: "asb:chunk", id: msg.id, b64: msg.b64 }));
        break;
      }
      case "vg:blobEnd": {
        const mime = msg.mime || "video/mp4";
        const filename = safeName(msg.name || "video", extOf("", mime));
        const r = await assembleAndDownload(msg.id, mime, filename, tabId);
        log(r.ok ? "download" : "error", `assembled ${filename}: ${r.ok ? "saved" : r.error}`);
        if (!r.ok && tabId) chrome.tabs.sendMessage(tabId, { type: "vg:blobSaveFailed", error: r.error }).catch(() => {});
        sendResponse(r);
        break;
      }
      case "vg:blobAbort": {
        await offscreen({ type: "asb:abort", id: msg.id });
        sendResponse({ ok: true });
        break;
      }

      case "vg:blobFailed":
        log("error", `page read failed: ${msg.error}`);
        if (tabId) chrome.tabs.sendMessage(tabId, { type: "vg:blobSaveFailed", error: msg.error }).catch(() => {});
        sendResponse({ ok: true });
        break;

      case "vg:log":
        log("content", msg.msg || "");
        sendResponse({ ok: true });
        break;

      case "vg:setFlag":
        await chrome.storage.session.set({ forceFallback: !!msg.value });
        log("test", `forceFallback=${!!msg.value}`);
        sendResponse({ ok: true });
        break;

      case "vg:logs":
        chrome.storage.session.get("vgLogs").then((s) => {
          const persisted = s.vgLogs || [];
          const merged = Array.from(new Set(persisted.concat(logs.slice(-30))));
          sendResponse({ ok: true, logs: merged.slice(-100) });
        }).catch(() => sendResponse({ ok: true, logs: logs.slice(-100) }));
        break;

      case "vg:downloads":
        chrome.downloads.search({}).then((d) => sendResponse({
          ok: true,
          downloads: d.map((x) => ({ id: x.id, state: x.state && x.state.current, error: x.error, file: (x.filename || "").split(/[\\/]/).pop(), bytes: x.bytesReceived, total: x.totalBytes }))
        }));
        break;

      case "vg:list": {
        // content scripts don't know their tab id - fall back to the sender
        const tabId = (sender.tab && sender.tab.id) != null ? sender.tab.id : msg.tabId;
        const key = await tabKey(tabId);
        const store = await chrome.storage.session.get(key);
        sendResponse({ ok: true, items: store[key] || [], logs: logs.slice(-120) });
        break;
      }

      default:
        sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      log("error", `handler ${msg && msg.type}: ${e.message}\n${(e.stack || "").split("\n")[1] || ""}`);
      try { sendResponse({ ok: false, error: e.message }); } catch (e2) {}
    }
  })();
  return true; // async sendResponse
});

// CDP debug access: chrome.runtime SW is evaluated by scripts/test_extension.py
self.__vgDebug = { get logs() { return logs.slice(-60); } };
