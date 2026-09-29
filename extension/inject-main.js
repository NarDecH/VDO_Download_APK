/**
 * VDO Grabber — MAIN-world injector (equivalent of app/core/detector.py DETECT_JS).
 * Runs on every page before anything else (manifest world:"MAIN"):
 *   1. hooks window.fetch / XMLHttpRequest to surface media requests
 *   2. captures MediaSource.appendBuffer data so MSE streams can be rebuilt
 *   3. reads blob: URLs on demand (file blobs only — see readBlob)
 * Talks to the ISOLATED content script via window.postMessage("__vg" envelope).
 */
(() => {
  if (window.__vgMain) return;
  window.__vgMain = true;

  const MEDIA_RE = /\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i;
  const CHUNK = 4 * 1024 * 1024; // bytes per base64 chunk posted to the content script

  function post(type, data) {
    try { window.postMessage(Object.assign({ __vg: "main", type }, data), "*"); } catch (e) {}
  }

  function abs(u) { try { return new URL(u, location.href).href; } catch (e) { return null; } }

  // ---- 1. network hooks -------------------------------------------------
  try {
    if (!window.__vgFetchOrig) {
      window.__vgFetchOrig = window.fetch;
      window.fetch = function (input, init) {
        try {
          const u = typeof input === "string" ? input : (input && input.url) || "";
          if (MEDIA_RE.test(u)) post("media", { url: abs(u), via: "fetch hook" });
        } catch (e) {}
        return window.__vgFetchOrig.apply(this, arguments);
      };
    }
    if (!window.XMLHttpRequest.prototype.__vgOpen) {
      const orig = window.XMLHttpRequest.prototype.open;
      window.XMLHttpRequest.prototype.__vgOpen = true;
      window.XMLHttpRequest.prototype.open = function (method, u) {
        try { if (MEDIA_RE.test(String(u))) post("media", { url: abs(u), via: "xhr hook" }); } catch (e) {}
        return orig.apply(this, arguments);
      };
    }
  } catch (e) {}

  // ---- 2. MediaSource capture (MSE streams inside blob: players) --------
  try {
    if (window.MediaSource && !MediaSource.prototype.__vgPatched) {
      MediaSource.prototype.__vgPatched = true;
      MediaSource.prototype.__vgParts = [];
      MediaSource.prototype.__vgMime = "";
      const origAdd = MediaSource.prototype.addSourceBuffer;
      MediaSource.prototype.addSourceBuffer = function (mime) {
        this.__vgMime = this.__vgMime || mime || "";
        const sb = origAdd.apply(this, arguments);
        const origAppend = sb.appendBuffer;
        sb.appendBuffer = function (buf) {
          try {
            const src = buf instanceof ArrayBuffer ? new Uint8Array(buf) : new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
            this.__vgParts = this.__vgParts || [];
            this.__vgParts.push(src.slice());
          } catch (e) {}
          return origAppend.apply(this, arguments);
        };
        // link the source buffer back to its MediaSource for assembly
        try { Object.defineProperty(sb, "__vgMs", { value: this, configurable: true }); } catch (e) {}
        return sb;
      };
    }
  } catch (e) {}

  // ---- helpers -----------------------------------------------------------
  function bufToB64Chunks(bytes, id, name, mime) {
    // post in ordered chunks; the SW reassembles. 0.5% overhead vs size cap in SW.
    let sent = 0, i = 0;
    const total = bytes.length;
    post("blobMeta", { id, name, mime, total });
    while (sent < total) {
      const slice = bytes.subarray(sent, Math.min(sent + CHUNK, total));
      let bin = "";
      const step = 0x8000;
      for (let p = 0; p < slice.length; p += step) {
        bin += String.fromCharCode.apply(null, slice.subarray(p, Math.min(p + step, slice.length)));
      }
      post("blobChunk", { id, index: i, count: Math.ceil(total / CHUNK), b64: btoa(bin) });
      sent += slice.length; i++;
    }
    post("blobDone", { id, chunks: i });
  }

  // ---- 3. on-demand blob:/http(s) reader -------------------------------
  function xhrGetBlob(url) {
    return new Promise((resolve, reject) => {
      try {
        const xhr = new XMLHttpRequest();
        xhr.open("GET", url, true);
        xhr.responseType = "blob";
        xhr.onload = () => (xhr.response && xhr.response.size ? resolve(xhr.response) : reject(new Error("XHR empty response")));
        xhr.onerror = () => reject(new Error("XHR network error"));
        xhr.send();
      } catch (e) { reject(e); }
    });
  }

  async function readBlob(id, url) {
    try {
      let blob = null;
      try {
        const resp = await window.__vgFetchOrig ? window.__vgFetchOrig(url) : fetch(url);
        if (resp && resp.ok && resp.blob) blob = await resp.blob();
      } catch (e) { /* fall through to XHR */ }
      if (!blob) blob = await xhrGetBlob(url);   // XHR handles blob: URLs reliably
      const buf = new Uint8Array(await blob.arrayBuffer());
      if (buf.length === 0) throw new Error("empty blob");
      const mime = blob.type || "video/mp4";
      bufToB64Chunks(buf, id, "", mime);
    } catch (e) {
      post("blobError", { id, error: String(e && e.message || e) });
    }
  }

  // direct http(s) media that chrome.downloads refused (expired signed URL,
  // picky CDN): re-fetch with the page's own credentials/CORS and stream it.
  async function readUrl(id, url) {
    if (!url || !/^https?:/i.test(url)) {
      post("blobError", { id, error: "readUrl: not an http(s) url" });
      return;
    }
    try {
      let blob = null, detail = "";
      try {
        const resp = await window.__vgFetchOrig ? window.__vgFetchOrig(url, { credentials: "include" }) : fetch(url, { credentials: "include" });
        if (resp && resp.ok && resp.blob) {
          blob = await resp.blob();
        } else {
          detail = `type=${resp && resp.type} status=${resp && resp.status}`;
        }
      } catch (e) {
        detail = "fetch threw: " + String(e && e.message || e);
      }
      if (!blob) {
        try { blob = await xhrGetBlob(url); } catch (e) { detail += " | xhr: " + String(e && e.message || e); }
      }
      if (!blob || !blob.size) throw new Error("readUrl failed: " + (detail || "empty"));
      const buf = new Uint8Array(await blob.arrayBuffer());
      const mime = blob.type || "";
      bufToB64Chunks(buf, id, "", mime);
    } catch (e) {
      post("blobError", { id, error: String(e && e.message || e) });
    }
  }

  // ---- 4. MSE assembled data --------------------------------------------
  async function readMse(id) {
    try {
      const mss = [];
      if (window.MediaSource) {
        const all = (MediaSource.prototype.__vgInstances = MediaSource.prototype.__vgInstances || []);
        for (const ms of all) mss.push(ms);
      }
      // collect captured chunks from every source buffer, pick the largest stream
      let bytes = null, mime = "video/mp4";
      for (const ms of mss) {
        const list = [];
        for (const sb of (ms.sourceBuffers || [])) {
          if (sb.__vgParts) for (const p of sb.__vgParts) list.push(p);
        }
        if (ms.__vgParts) for (const p of ms.__vgParts) list.push(p);
        if (!list.length) continue;
        const size = list.reduce((n, p) => n + p.length, 0);
        if (!bytes || size > bytes.length) { bytes = list; mime = ms.__vgMime || mime; }
      }
      if (!bytes || !bytes.length) throw new Error("no MSE data captured");
      const total = bytes.reduce((n, p) => n + p.length, 0);
      const merged = new Uint8Array(total);
      let off = 0;
      for (const p of bytes) { merged.set(p, off); off += p.length; }
      bufToB64Chunks(merged, id, "", mime);
    } catch (e) {
      post("blobError", { id, error: String(e && e.message || e) });
    }
  }

  // track MediaSource instances for readMse
  try {
    if (window.MediaSource) {
      const origCtor = window.MediaSource;
      window.MediaSource = function (...args) {
        const ms = new origCtor(...args);
        (origCtor.prototype.__vgInstances = origCtor.prototype.__vgInstances || []).push(ms);
        return ms;
      };
      window.MediaSource.prototype = origCtor.prototype;
      Object.setPrototypeOf(window.MediaSource, origCtor);
      window.MediaSource.isTypeSupported = origCtor.isTypeSupported.bind(origCtor);
      try { Object.defineProperty(window.MediaSource, "name", { value: "MediaSource" }); } catch (e) {}
    }
  } catch (e) {}

  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || e.data.__vg !== "ui") return;
    if (e.data.type === "readBlob") readBlob(e.data.id, e.data.url);
    else if (e.data.type === "readUrl") readUrl(e.data.id, e.data.url);
    else if (e.data.type === "readMse") readMse(e.data.id);
  });
})();
