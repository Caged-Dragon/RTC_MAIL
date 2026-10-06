// RT Crackers Mail - vanilla JS, no build step. All text is inserted with textContent (never innerHTML).
const $ = (s) => document.querySelector(s);
const S = { cfg: null, box: "admin", view: "inbox", openId: null, counts: {}, q: "", started: false, timer: null };
async function applyDbTheme() {
  try {
    const res = await fetch('/api/theme'); const t = await res.json();
    const root = document.documentElement;
    const vars = { '--rtc-bg':t.background_color,'--rtc-surface':t.surface_color,'--rtc-text':t.text_color,'--rtc-muted':t.muted_text_color,'--rtc-heading':t.heading_color,'--rtc-primary':t.primary_color,'--rtc-accent':t.accent_color,'--rtc-border':t.border_color,'--rtc-header':t.header_color,'--rtc-footer':t.footer_color,'--rtc-nav':t.nav_background_color,'--rtc-nav-text':t.nav_text_color,'--rtc-input':t.input_background_color,'--rtc-input-border':t.input_border_color };
    for (const [k,v] of Object.entries(vars)) if (v) root.style.setProperty(k,v);
    const old = document.getElementById('rtc-component-theme'); if (old) old.remove();
    const style = document.createElement('style'); style.id='rtc-component-theme';
    const map = {font_family:'font-family',font_size:'font-size',font_weight:'font-weight',line_height:'line-height',letter_spacing:'letter-spacing',text_color:'color',background_color:'background-color',border_color:'border-color',border_width:'border-width',border_radius:'border-radius',box_shadow:'box-shadow',padding:'padding',margin:'margin',width:'width',max_width:'max-width',text_align:'text-align'};
    style.textContent=(t.components||[]).map(c=>{const key=String(c.component_key||'').replace(/[^a-zA-Z0-9_-]/g,''); if(!key)return ''; const d=Object.entries(map).map(([k,p])=>c[k]&&typeof c[k]==='string'&&!/[<>]/.test(c[k])?`${p}:${c[k]}`:'').filter(Boolean).join(';'); return d?`[data-rtc-component=\"${key}\"]{${d}}`:'';}).join('');
    document.head.appendChild(style);
  } catch {}
}
const TOK = "rtmail.session";
const session = {
  get() { try { return JSON.parse(localStorage.getItem(TOK) || "null"); } catch { return null; } },
  set(v) { try { localStorage.setItem(TOK, JSON.stringify(v)); } catch { /* storage blocked */ } },
  clear() { try { localStorage.removeItem(TOK); } catch { /* ignore */ } },
};
// Fixed colours for the four required mailboxes; every other mailbox gets a stable colour from its name.
const FIXED = { admin: "#d8372b", sales: "#e19a14", support: "#1b9c8c", noreply: "#7b8394" };
function colorOf(id) {
  if (FIXED[id]) return FIXED[id];
  let n = 0;
  for (const ch of id) n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  return `hsl(${n % 360} 55% 42%)`;
}

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}

async function rawApi(path, opts = {}, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch("/api" + path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  return { res, data };
}
const errText = (data, res) => {
  const d = data.detail;
  return Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || `Request failed (${res.status})`;
};

async function refreshSession() {
  const s = session.get();
  if (!s || !s.refresh_token) return false;
  const { res, data } = await rawApi("/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token: s.refresh_token }) });
  if (!res.ok) return false;
  session.set({ ...s, access_token: data.access_token, refresh_token: data.refresh_token });
  return true;
}

async function api(path, opts = {}) {
  let s = session.get();
  let { res, data } = await rawApi(path, opts, s && s.access_token);
  if (res.status === 401 && (await refreshSession())) {
    s = session.get();
    ({ res, data } = await rawApi(path, opts, s && s.access_token));
  }
  if (res.status === 401) { showLogin(); throw new Error(errText(data, res)); }
  if (!res.ok) throw new Error(errText(data, res));
  return data;
}

let toastTimer;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), isErr ? 6000 : 2800);
}

function fmtShort(iso) {
  const d = new Date(iso), now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.toLocaleDateString([], d.getFullYear() === now.getFullYear() ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
}
const fmtFull = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const boxInfo = (id) => S.cfg.mailboxes.find((m) => m.id === id);

// ---------- sidebar ----------
function renderBoxes() {
  $("#boxes").replaceChildren(...S.cfg.mailboxes.map((m) => {
    const n = S.counts[m.id] || 0;
    const b = h("button", { class: "box", role: "tab", "aria-selected": String(m.id === S.box), onclick: () => selectBox(m.id) },
      h("div", {}, h("strong", {}, m.label.replace("RT Crackers ", "")), h("small", {}, m.address)),
      n ? h("span", { class: "badge" }, n) : null);
    b.style.setProperty("--c", colorOf(m.id));
    return b;
  }));
  $("#app").style.setProperty("--accent", colorOf(S.box));
  const n = S.counts[S.box] || 0;
  $("#inboxCount").textContent = n ? n : "";
  document.querySelectorAll(".nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === S.view));
}

async function refreshCounts() {
  try { S.counts = await api("/counts"); } catch { /* ignore */ }
  renderBoxes();
}

function selectBox(id) {
  S.box = id; S.openId = null;
  if (S.view === "compose") S.view = "inbox";
  setView(S.view);
}

// ---------- views ----------
function setView(view) {
  S.view = view;
  $("#app").classList.toggle("composing", view === "compose");
  renderBoxes();
  if (view === "compose") return renderCompose({ from: S.box });
  S.openId = null;
  $("#listTitle").textContent = view === "inbox" ? "Inbox" : "Sent";
  showPlaceholder();
  loadList();
}

function showPlaceholder() {
  const a = boxInfo(S.box).address;
  $("#readerPane").replaceChildren(h("div", { class: "placeholder" }, h("div", {}, a), h("div", {}, "Select a message to read it.")));
}

async function loadList() {
  const url = `/messages?mailbox=${S.box}&folder=${S.view}&q=${encodeURIComponent(S.q)}`;
  let items;
  try { items = await api(url); } catch (e) { return toast(e.message, true); }
  const rows = $("#rows");
  if (!items.length) {
    const msg = S.q ? "No messages match your search."
      : S.view === "inbox" ? `No mail for ${boxInfo(S.box).address} yet.\nUse “Sync inbox” to check for new mail.`
      : "Nothing sent from this address yet.";
    return rows.replaceChildren(h("div", { class: "empty", style: "white-space:pre-line" }, msg));
  }
  rows.replaceChildren(...items.map((m) => {
    const who = S.view === "inbox" ? m.from_name || m.from_address : "To: " + m.to_addrs.join(", ");
    const row = h("button", { class: "row" + (m.is_read ? "" : " unread") + (m.id === S.openId ? " sel" : ""), "data-id": m.id, onclick: () => openMessage(m.id) },
      h("div", { class: "row-top" }, h("span", { class: "row-who" }, who), h("span", { class: "row-date" }, fmtShort(m.created_at))),
      h("div", { class: "row-sub" }, m.subject || "(no subject)"),
      h("div", { class: "row-snip" }, m.snippet));
    return row;
  }));
}

async function openMessage(id) {
  let m;
  try { m = await api(`/messages/${id}`); } catch (e) { return toast(e.message, true); }
  S.openId = id;
  document.querySelectorAll(".row").forEach((r) => {
    r.classList.toggle("sel", Number(r.dataset.id) === id);
    if (Number(r.dataset.id) === id) r.classList.remove("unread");
  });
  refreshCounts();

  const meta = [["From", m.from_name ? `${m.from_name} <${m.from_address}>` : m.from_address], ["To", m.to_addrs.join(", ")]];
  if (m.cc_addrs.length) meta.push(["Cc", m.cc_addrs.join(", ")]);
  if (m.bcc_addrs.length) meta.push(["Bcc", m.bcc_addrs.join(", ")]);
  meta.push(["Date", fmtFull(m.created_at)]);

  const head = h("div", { class: "msg-head" },
    h("h1", {}, m.subject || "(no subject)"),
    h("div", { class: "meta" }, meta.map(([k, v]) => [h("b", {}, k), h("span", {}, v)])),
    m.folder === "inbox" ? h("div", { class: "msg-actions" }, h("button", { class: "btn", onclick: () => reply(m) }, "Reply")) : null);

  let body;
  if (m.folder === "inbox" && m.html_body) {
    // Received HTML is untrusted: sandboxed iframe, no scripts, CSP blocks everything except https images + inline CSS.
    const doc = `<!doctype html><meta charset="utf-8"><base target="_blank">` +
      `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline'">` +
      `<style>body{font:14px/1.55 system-ui,sans-serif;margin:16px 28px;color:#222}img{max-width:100%;height:auto}</style>` + m.html_body;
    body = h("div", { class: "msg-body framed" }, h("iframe", { class: "mail-frame", sandbox: "allow-popups allow-popups-to-escape-sandbox", srcdoc: doc, title: "Message" }));
  } else {
    body = h("div", { class: "msg-body" }, h("pre", {}, m.text_body || "(empty message)"));
  }
  $("#readerPane").replaceChildren(head, body);
}

function reply(m) {
  const subj = /^re:/i.test(m.subject) ? m.subject : "Re: " + m.subject;
  const quoted = (m.text_body || "").split("\n").map((l) => "> " + l).join("\n");
  S.view = "compose";
  $("#app").classList.add("composing");
  renderBoxes();
  renderCompose({ from: m.mailbox, to: m.from_address, subject: subj, replyTo: m.id,
    body: `\n\nOn ${fmtFull(m.created_at)}, ${m.from_name || m.from_address} wrote:\n${quoted}` });
}

// ---------- compose ----------
function renderCompose(p = {}) {
  const from = h("select", { id: "cFrom" }, S.cfg.mailboxes.filter((m) => m.can_send).map((m) => h("option", { value: m.id, selected: m.id === (p.from || S.box) }, m.address)));
  const to = h("input", { id: "cTo", value: p.to || "", placeholder: "name@example.com, another@example.com", autocomplete: "off" });
  const cc = h("input", { id: "cCc", placeholder: "optional" });
  const bcc = h("input", { id: "cBcc", placeholder: "optional" });
  const subject = h("input", { id: "cSubject", value: p.subject || "", placeholder: "Subject" });
  const body = h("textarea", { id: "cBody", placeholder: "Write your message…" });
  body.value = p.body || "";
  const send = h("button", { class: "btn", id: "cSend" }, "Send");
  const field = (label, input) => h("div", { class: "field" }, h("label", { for: input.id }, label), input);

  from.addEventListener("change", () => $("#app").style.setProperty("--accent", colorOf(from.value)));
  from.dispatchEvent(new Event("change"));

  send.addEventListener("click", async () => {
    send.disabled = true; send.textContent = "Sending…";
    try {
      await api("/send", { method: "POST", body: JSON.stringify({ mailbox: from.value, to: to.value, cc: cc.value, bcc: bcc.value, subject: subject.value, body: body.value, reply_to_id: p.replyTo || null }) });
      toast(`Sent from ${boxInfo(from.value).address}`);
      S.box = from.value; S.q = ""; $("#search").value = "";
      setView("sent");
    } catch (e) {
      toast(e.message, true);
      send.disabled = false; send.textContent = "Send";
    }
  });

  $("#readerPane").replaceChildren(h("div", { class: "compose" },
    h("h2", {}, p.replyTo ? "Reply" : "New message"),
    field("From", from), field("To", to), field("Cc", cc), field("Bcc", bcc), field("Subject", subject),
    body,
    h("div", { class: "compose-foot" }, send, h("span", { class: "hint" }, "Sent as plain text with a simple HTML copy."))));
  (p.to ? body : to).focus();
}

// ---------- sync ----------
async function sync(silent = false, full = false) {
  const btn = $("#syncBtn");
  btn.disabled = true; btn.textContent = "Syncing…";
  try {
    const r = await api("/sync" + (full ? "?full=1" : ""), { method: "POST" });
    if (r.new) toast(`${r.new} new message${r.new > 1 ? "s" : ""}${r.partial ? " (more to download, sync again)" : ""}`);
    else if (!silent) toast("Inbox is up to date");
    if (r.failed) toast(`${r.failed} message(s) could not be downloaded; they will be retried on the next sync.`, true);
    await refreshCounts();
    if (S.view === "inbox" && !S.openId) loadList();
  } catch (e) {
    if (!silent) toast(e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "Sync inbox";
  }
}

// ---------- login / boot ----------
function showLogin() {
  clearInterval(S.timer); S.timer = null;
  $("#login").hidden = false;
  $("#lEmail").focus();
}

async function doLogin() {
  const btn = $("#lBtn"), err = $("#lErr");
  err.textContent = ""; btn.disabled = true; btn.textContent = "Signing in…";
  try {
    const { res, data } = await rawApi("/auth/login", { method: "POST", body: JSON.stringify({ email: $("#lEmail").value, password: $("#lPass").value }) });
    if (!res.ok) throw new Error(errText(data, res));
    session.set(data);
    $("#lPass").value = "";
    $("#login").hidden = true;
    await start();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false; btn.textContent = "Sign in";
  }
}

function signOut() {
  session.clear(); S.cfg = null; S.counts = {}; $("#rows").replaceChildren(); $("#readerPane").replaceChildren();
  showLogin();
}

async function start() {
  try { S.cfg = await api("/config"); } catch (e) {
    if (!session.get()) return;           // api() already showed the login screen
    session.clear(); showLogin(); $("#lErr").textContent = e.message; return;
  }
  if (!S.cfg.mailboxes.some((m) => m.id === S.box)) S.box = S.cfg.mailboxes[0] ? S.cfg.mailboxes[0].id : "";
  $("#who").textContent = S.cfg.user;
  const st = $("#status");
  if (S.cfg.resend_configured) { st.textContent = "Resend connected · auto-sync every minute"; st.className = "status"; }
  else { st.textContent = "RESEND_API_KEY is not set on the server."; st.className = "status warn"; }
  await refreshCounts();
  setView("inbox");
  if (S.cfg.resend_configured) { sync(true); S.timer = setInterval(() => sync(true), 60000); }
}

(function init() {
  applyDbTheme();
  $("#composeBtn").addEventListener("click", () => { if (!S.cfg) return; S.view = "compose"; $("#app").classList.add("composing"); renderBoxes(); renderCompose({ from: S.box }); });
  document.querySelectorAll(".nav button").forEach((b) => b.addEventListener("click", () => S.cfg && setView(b.dataset.view)));
  $("#syncBtn").addEventListener("click", () => sync(false));
  $("#fullSyncBtn").addEventListener("click", () => sync(false, true));
  $("#signOutBtn").addEventListener("click", signOut);
  $("#lBtn").addEventListener("click", doLogin);
  $("#lPass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("#lEmail").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#lPass").focus(); });
  let t;
  $("#search").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { S.q = e.target.value; loadList(); }, 250); });
  if (session.get()) start(); else showLogin();
})();
