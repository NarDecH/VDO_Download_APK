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
      chrome.runtime.sendMessage({ type: "vg:download", item: { url: u, kind: b.getAttribute("data-k"), name: tab.title || "video", page: tab.url } });
      b.textContent = "ส่งแล้ว ✓";
    }));
    document.querySelectorAll(".cp").forEach((b) => b.addEventListener("click", () => navigator.clipboard.writeText(b.getAttribute("data-u"))));
  });
}

$("#refresh").addEventListener("click", load);
load();
