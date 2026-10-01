const $ = (s) => document.querySelector(s);
const esc = (s) => String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function load() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  $("#tabHost").textContent = (new URL(tab.url || "about:blank").host || "").slice(0, 24);
  chrome.runtime.sendMessage({ type: "vg:list", tabId: tab.id }, (resp) => {
    if (chrome.runtime.lastError || !resp || !resp.ok) {
      $("#list").innerHTML = '<div class="empty">สื่อสารกับ background ไม่ได้ — รีโหลดหน้าเว็บแล้วลองใหม่</div>';
      return;
    }
    const items = resp.items || [];
    $("#list").innerHTML = items.length
      ? items.slice().reverse().map((m) => `
        <div class="item">
          <b>${esc(m.label || m.kind)}</b>
          <small>${esc(m.url.slice(0, 120))}</small>
          <div class="row">
            <button class="dl" data-u="${esc(m.url)}" data-k="${esc(m.kind)}">⬇ ดาวน์โหลด</button>
            <button class="cp" data-u="${esc(m.url)}">คัดลอก</button>
          </div>
        </div>`).join("")
      : '<div class="empty">ยังไม่พบวิดีโอ — เล่นวิดีโอในหน้าแล้วรีเฟรช</div>';
    $("#logs").textContent = (resp.logs || []).join("\n") || "—";
    document.querySelectorAll(".dl").forEach((b) => b.addEventListener("click", () => {
      const u = b.getAttribute("data-u");
      const k = b.getAttribute("data-k");
      b.textContent = "⏳ กำลังส่ง…";
      // tabId lets the SW resolve embed/page candidates against the live tab
      chrome.runtime.sendMessage({ type: "vg:download", tabId: tab.id, item: { url: u, kind: k, name: tab.title || "video", page: tab.url } }, (resp) => {
        if (chrome.runtime.lastError || !resp) { b.textContent = "ส่งแล้ว ✓"; return; }
        // v1.1.9: ok+hls = the extension assembled the HLS stream itself (.ts);
        // hls without ok (DASH / encrypted HLS) still needs the desktop app
        if (resp.ok && resp.hls) b.textContent = "ส่งแล้ว ✓ (HLS)";
        else if (resp.hls) b.textContent = "📺 ใช้แอปเดสก์ท็อป";
        else if (resp.page) b.textContent = "⚠️ ไม่พบวิดีโอ";
        else if (resp.ok) b.textContent = "ส่งแล้ว ✓";
        else b.textContent = "ล้มเหลว";
      });
    }));
    document.querySelectorAll(".cp").forEach((b) => b.addEventListener("click", () => navigator.clipboard.writeText(b.getAttribute("data-u"))));
  });
}

$("#refresh").addEventListener("click", load);
load();

// ---------------- site exclusions (v1.1.3) ---------------------------------
async function renderExclusions() {
  chrome.runtime.sendMessage({ type: "vg:exclusions" }, (resp) => {
    if (chrome.runtime.lastError || !resp || !resp.ok) return;
    const list = resp.patterns || [];
    $("#exList").innerHTML = list.length
      ? list.map((p) => `<div class="ex"><code>${esc(p)}</code><button data-rm="${esc(p)}">ลบ</button></div>`).join("")
      : '<div class="exhint">ยังไม่มีรายการยกเว้น</div>';
    document.querySelectorAll(".exlist button[data-rm]").forEach((b) => b.addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "vg:exclusion:remove", pattern: b.getAttribute("data-rm") }, renderExclusions);
    }));
  });
}

$("#exAdd").addEventListener("click", () => {
  const v = $("#exInput").value.trim();
  if (!v) return;
  chrome.runtime.sendMessage({ type: "vg:exclusion:add", pattern: v }, (resp) => {
    if (resp && resp.ok) { $("#exInput").value = ""; renderExclusions(); }
  });
});
$("#exInput").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#exAdd").click(); });
$("#exAddHost").addEventListener("click", () => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const t = tabs && tabs[0];
    if (!t || !t.url) return;
    try {
      // hostname (NOT host): bare-domain patterns survive ports
      const host = new URL(t.url).hostname;
      chrome.runtime.sendMessage({ type: "vg:exclusion:add", pattern: host }, renderExclusions);
    } catch (e) {}
  });
});

// ---- exclusion import/export (v1.2.1) - same JSON file as the Windows app
$("#exExport").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "vg:exclusion:export" }, (resp) => {
    if (chrome.runtime.lastError || !resp || !resp.ok) return;
    const blob = new Blob([resp.json], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "vdograbber-exclusions.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
});
$("#exImport").addEventListener("click", () => $("#exImportFile").click());
$("#exImportFile").addEventListener("change", (e) => {
  const f = e.target.files && e.target.files[0];
  e.target.value = ""; // allow re-picking the same file later
  if (!f) return;
  f.text().then((txt) => chrome.runtime.sendMessage({ type: "vg:exclusion:import", json: txt }, (resp) => {
    if (resp && resp.ok) renderExclusions();
  }));
});

// ---- exclusion cloud sync (v1.1.7) - one secret gist, shared with the app
const GIST_FILENAME = "vdograbber-exclusions.json";
const gistToken = async () => (await chrome.storage.local.get("vgGithubPat")).vgGithubPat || "";

async function renderCloudStatus() {
  const s = await chrome.storage.local.get(["vgGithubPat", "vgGistId"]);
  const list = await new Promise((res) => chrome.runtime.sendMessage({ type: "vg:exclusions" }, (r) => res((r && r.patterns) || [])));
  $("#exCloudStatus").textContent =
    (s.vgGithubPat ? "token ✓" : "ไม่มี token") + (s.vgGistId ? " · gist ✓" : "") + " · " + list.length + " รายการ";
}

$("#exSaveToken").addEventListener("click", async () => {
  const v = $("#exPat").value.trim();
  if (!v) return;
  await chrome.storage.local.set({ vgGithubPat: v });
  $("#exPat").value = "";
  renderCloudStatus();
});

$("#exCloudPush").addEventListener("click", async () => {
  const token = await gistToken();
  if (!token) { $("#exCloudStatus").textContent = "ใส่ token ก่อน"; return; }
  $("#exCloudStatus").textContent = "กำลังอัปโหลด…";
  chrome.runtime.sendMessage({ type: "vg:exclusion:export" }, async (doc) => {
    if (!doc || !doc.ok) { $("#exCloudStatus").textContent = "export ไม่สำเร็จ"; return; }
    const gistId = (await chrome.storage.local.get("vgGistId")).vgGistId || "";
    const body = {
      description: "VDO Grabber exclusion list (automatic sync)",
      files: { [GIST_FILENAME]: { content: doc.json } },
    };
    try {
      const resp = await fetch(gistId ? ("https://api.github.com/gists/" + gistId) : "https://api.github.com/gists", {
        method: gistId ? "PATCH" : "POST",
        headers: { "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json" },
        body: JSON.stringify(gistId ? body : { ...body, public: false }),
      });
      if (!resp.ok) { $("#exCloudStatus").textContent = "github " + resp.status; return; }
      const out = await resp.json();
      if (!gistId && out.id) await chrome.storage.local.set({ vgGistId: out.id });
      $("#exCloudStatus").textContent = "อัปโหลดแล้ว " + (doc.count || 0) + " รายการ";
    } catch (e) { $("#exCloudStatus").textContent = "ผิดพลาด: " + e.message; }
  });
});

$("#exCloudPull").addEventListener("click", async () => {
  const token = await gistToken();
  const gistId = (await chrome.storage.local.get("vgGistId")).vgGistId || "";
  if (!token || !gistId) { $("#exCloudStatus").textContent = "ต้องมี token + gist (กดอัปโหลดครั้งแรกก่อน)"; return; }
  $("#exCloudStatus").textContent = "กำลังดึง…";
  try {
    const resp = await fetch("https://api.github.com/gists/" + gistId, {
      headers: { "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json" },
    });
    if (!resp.ok) { $("#exCloudStatus").textContent = "github " + resp.status; return; }
    const out = await resp.json();
    const text = out.files && out.files[GIST_FILENAME] && out.files[GIST_FILENAME].content;
    if (!text) { $("#exCloudStatus").textContent = "gist ไม่มีไฟล์รายการ"; return; }
    chrome.runtime.sendMessage({ type: "vg:exclusion:import", json: text }, (r) => {
      if (r && r.ok) { renderExclusions(); $("#exCloudStatus").textContent = "ดึงแล้ว +" + r.added + " (รวม " + r.total + ")"; }
      else $("#exCloudStatus").textContent = "เอกสารใน gist ไม่ถูกต้อง";
    });
  } catch (e) { $("#exCloudStatus").textContent = "ผิดพลาด: " + e.message; }
});
renderExclusions();
renderCloudStatus();
