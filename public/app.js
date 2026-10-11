// RT Crackers Mail - vanilla JS, no build step.
const $ = (s) => document.querySelector(s);
const SYNC_INTERVAL_MS = 60000;
const AUTOSAVE_MS = 1500;
const PAGE_SIZE = 50;
// Detects remote resources in mail HTML (src/srcset/background attributes and CSS url()).
const REMOTE_IMG_RE = /(?:src|srcset|background)\s*=\s*["']?\s*(?:https?:)?\/\/|url\(\s*["']?\s*(?:https?:)?\/\//i;

const TOK = "rtmail.session";
const session = {
  get() { try { return JSON.parse(localStorage.getItem(TOK) || "null"); } catch { return null; } },
  set(v) { try { localStorage.setItem(TOK, JSON.stringify(v)); } catch {} },
  clear() { try { localStorage.removeItem(TOK); } catch {} },
};


const S = {
  cfg: null, box: "admin", view: "inbox", openId: null, openMsg: null,
  counts: {}, drafts: {}, q: "", timer: null, listReq: 0, syncing: false,
  compose: null, prevView: "inbox",
  items: [], sel: new Set(), cur: -1, hasMore: false, loading: false,
};
const TITLES = { inbox: "Inbox", starred: "Starred", sent: "Sent", drafts: "Drafts", trash: "Trash" };
const EMPTY = {
  inbox: "Your inbox is empty.",
  starred: "No starred messages. Click the star next to a message to find it here.",
  sent: "No sent messages yet.",
  drafts: "No drafts.",
  trash: "Trash is empty.",
};
const enc = encodeURIComponent;

async function applyCompanyBrand() {
  try {
    const res = await fetch("/api/company");
    if (!res.ok) return;
    const company = await res.json();
    document.querySelectorAll(".brand").forEach((brand) => {
      const name = brand.querySelector(".company-brand-name");
      const logo = brand.querySelector(".brand-logo");
      if (name && company.company_name) name.textContent = company.company_name;
      if (logo) {
        if (company.logo_url) { logo.src = company.logo_url; logo.alt = company.company_name ? company.company_name + " logo" : "Company logo"; logo.hidden = false; }
        else logo.hidden = true;
      }
    });
  } catch {}
}

async function applyDbTheme() {
  try {
    const res = await fetch("/api/theme");
    if (!res.ok) return;
    const t = await res.json();
    const root = document.documentElement;
    const vars = {
      "--rtc-bg": t.background_color, "--rtc-surface": t.surface_color,
      "--rtc-text": t.text_color, "--rtc-muted": t.muted_text_color,
      "--rtc-heading": t.heading_color, "--rtc-primary": t.primary_color,
      "--rtc-accent": t.accent_color, "--rtc-border": t.border_color,
      "--rtc-header": t.header_color, "--rtc-nav": t.nav_background_color,
      "--rtc-nav-text": t.nav_text_color
    };
    for (const [k, v] of Object.entries(vars)) if (v) root.style.setProperty(k, v);
  } catch {}
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
  let res;
  try {
    res = await fetch("/api" + path, { ...opts, headers });
  } catch {
    // Offline / DNS / server down: surface a readable message instead of a raw TypeError.
    return { res: { ok: false, status: 0 }, data: { detail: "Network error. Check your connection and try again." } };
  }
  const data = await res.json().catch(() => ({}));
  return { res, data };
}

const errText = (data, res) => {
  const d = data.detail;
  return Array.isArray(d) ? d.map((x) => x.msg).join("; ") : d || `Request failed (${res.status})`;
};

// Several requests can hit a 401 at once (e.g. list + counts). Share one refresh call between
// them, otherwise rotating refresh tokens get invalidated and the user is signed out.
let refreshing = null;
function refreshSession() {
  if (!refreshing) {
    refreshing = (async () => {
      const s = session.get();
      if (!s || !s.refresh_token) return false;
      const { res, data } = await rawApi("/auth/refresh", {
        method: "POST",
        body: JSON.stringify({ refresh_token: s.refresh_token })
      });
      if (!res.ok) return false;
      session.set({ ...s, access_token: data.access_token, refresh_token: data.refresh_token });
      return true;
    })().finally(() => { refreshing = null; });
  }
  return refreshing;
}

async function api(path, opts = {}) {
  let s = session.get();
  let { res, data } = await rawApi(path, opts, s && s.access_token);
  if (res.status === 401 && (await refreshSession())) {
    s = session.get();
    ({ res, data } = await rawApi(path, opts, s && s.access_token));
  }
  if (res.status === 401) { session.clear(); showLogin(); throw new Error(errText(data, res)); }
  if (!res.ok) throw new Error(errText(data, res));
  return data;
}


// ---------------------------------------------------------------- toast, dates, icons
let toastTimer;
function toast(msg, isErr = false, action = null) {
  const t = $("#toast");
  t.replaceChildren(msg);
  if (action) {
    t.append(h("button", { type: "button", class: "link", onclick: () => { t.className = "toast"; action.run(); } }, action.label));
  }
  t.className = "toast show" + (isErr ? " err" : "") + (action ? " has-action" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), action ? 7000 : isErr ? 6000 : 2800);
}

const fmtShort = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

// Gmail-style: time today, "Mar 4" this year, short date before that.
function fmtListDate(iso) {
  const d = new Date(iso), n = new Date();
  if (isNaN(d)) return "";
  if (d.toDateString() === n.toDateString()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (d.getFullYear() === n.getFullYear()) return d.toLocaleDateString([], { month: "short", day: "numeric" });
  return d.toLocaleDateString([], { year: "2-digit", month: "numeric", day: "numeric" });
}

const ICONS = {
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
  send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  file: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
  restore: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
  back: '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
  mail: '<path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><polyline points="22,6 12,13 2,6"/>',
  "mail-open": '<path d="M21.2 8.4l-9.2-6-9.2 6A2 2 0 0 0 2 10v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-9a2 2 0 0 0-.8-1.6z"/><polyline points="22 10 12 15 2 10"/>',
  reply: '<polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/>',
  "reply-all": '<polyline points="7 17 2 12 7 7"/><polyline points="12 17 7 12 12 7"/><path d="M22 18v-2a4 4 0 0 0-4-4H7"/>',
  forward: '<polyline points="15 14 20 9 15 4"/><path d="M4 20v-7a4 4 0 0 1 4-4h12"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
  close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
function icon(name) {
  const el = h("span", { class: "ic", "aria-hidden": "true" });
  el.innerHTML = svg(name);   // constant markup from ICONS above, never user data
  return el;
}
function iconBtn(name, label, onclick, cls = "") {
  return h("button", { type: "button", class: "ib " + cls, title: label, "aria-label": label, onclick }, icon(name));
}
function initials(s) {
  const parts = (s || "?").replace(/<.*?>/g, "").trim().split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}
function avatar(name) {
  let hash = 0;
  for (const ch of name || "?") hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const el = h("div", { class: "avatar", "aria-hidden": "true" }, initials(name));
  el.style.background = `hsl(${hash % 360} 45% 42%)`;
  return el;
}

function readerPlaceholder(text = "Select a message to read it.") {
  $("#readerPane").replaceChildren(h("div", { class: "placeholder" }, text));
}

function renderNav() {
  document.querySelectorAll(".nav button").forEach((b) => {
    const on = b.dataset.view === S.view;
    b.classList.toggle("on", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
}

function renderBoxes() {
  $("#boxes").replaceChildren(...S.cfg.mailboxes.map((m) => {
    const n = S.counts[m.id] || 0;
    const b = h("button", {
      type: "button", class: "box", role: "tab", "aria-selected": String(m.id === S.box),
      onclick: () => selectBox(m.id)
    },
      h("div", {}, h("strong", {}, m.label.replace("RT Crackers ", "")), h("small", {}, m.address)),
      n ? h("span", { class: "badge", "aria-label": `${n} unread` }, n) : null);
    b.style.setProperty("--c", m.id === "admin" ? "#d8372b" : "#7b8394");
    return b;
  }));
  $("#inboxCount").textContent = S.counts[S.box] || "";
  $("#draftCount").textContent = S.drafts[S.box] || "";
}

// ---------------------------------------------------------------- message list
function rowWho(m) {
  if (S.view === "sent" || S.view === "drafts" || m.folder === "sent") {
    return m.to_addrs.length ? "To: " + m.to_addrs.join(", ") : "(no recipient)";
  }
  return m.from_name || m.from_address;
}

const PROBLEMS = { bounced: "Bounced", failed: "Failed", complained: "Spam report" };

function rowNode(m, i) {
  const isDraft = S.view === "drafts";
  const chk = h("input", { type: "checkbox", class: "chk", "aria-label": "Select message", onchange: (e) => toggleSel(m.id, e.target.checked) });
  chk.checked = S.sel.has(m.id);
  const star = isDraft ? h("span", { class: "star-gap" }) : h("button", {
    type: "button", class: "star" + (m.is_starred ? " on" : ""), "aria-pressed": String(!!m.is_starred),
    "aria-label": m.is_starred ? "Unstar" : "Star", onclick: () => toggleStar(m.id)
  }, icon("star"));

  const who = isDraft ? [h("span", { class: "draft-tag" }, "Draft"), rowWho(m)] : rowWho(m);
  const main = h("button", { type: "button", class: "row-main", onclick: () => { S.cur = i; (isDraft ? openDraft : openMessage)(m.id); } },
    h("div", { class: "row-top" }, h("span", { class: "row-who" }, who), h("span", { class: "row-date" }, fmtListDate(m.created_at))),
    h("div", { class: "row-sub" }, m.subject || "(no subject)", PROBLEMS[m.status] ? h("span", { class: "chip bad" }, PROBLEMS[m.status]) : null),
    h("div", { class: "row-snip" }, m.snippet));

  const acts = [];
  if (S.view === "trash") {
    acts.push(iconBtn("restore", "Restore", () => bulk("restore", [m.id])), iconBtn("trash", "Delete forever", () => bulk("delete_forever", [m.id])));
  } else if (isDraft) {
    acts.push(iconBtn("trash", "Delete draft", () => bulk("delete_draft", [m.id])));
  } else {
    acts.push(iconBtn("trash", "Trash", () => bulk("trash", [m.id])));
    if (m.folder === "inbox") acts.push(iconBtn(m.is_read ? "mail" : "mail-open", m.is_read ? "Mark as unread" : "Mark as read", () => bulk(m.is_read ? "unread" : "read", [m.id])));
  }
  return h("div", {
    class: "row" + (m.is_read ? "" : " unread") + (m.id === S.openId ? " sel" : "") + (S.sel.has(m.id) ? " selected" : "") + (i === S.cur ? " cur" : ""),
    "data-id": m.id
  }, chk, star, main, h("div", { class: "row-actions" }, acts));
}

function renderRows() {
  const rows = $("#rows");
  if (!S.items.length) {
    const text = S.loading ? "Loading…" : S.q ? "No messages match your search." : EMPTY[S.view] || "";
    rows.replaceChildren(h("div", { class: "empty" }, text));
  } else {
    const nodes = S.items.map(rowNode);
    if (S.hasMore) nodes.push(h("button", { type: "button", class: "more", onclick: () => loadList(true) }, "Load more"));
    rows.replaceChildren(...nodes);
  }
  renderToolbar();
}

async function loadList(append = false) {
  const req = ++S.listReq;   // ignore responses that arrive after a newer request was started
  const offset = append ? S.items.length : 0;
  let items;
  try {
    items = await api(`/messages?mailbox=${enc(S.box)}&folder=${S.view}&q=${enc(S.q)}&limit=${PAGE_SIZE}&offset=${offset}`);
  } catch (e) {
    if (req === S.listReq) { S.loading = false; renderRows(); toast(e.message, true); }
    return;
  }
  if (req !== S.listReq || S.view === "compose") return;
  S.loading = false;
  if (append) {
    const have = new Set(S.items.map((m) => m.id));
    S.items = S.items.concat(items.filter((m) => !have.has(m.id)));
  } else {
    S.items = items;
    const ids = new Set(items.map((m) => m.id));
    S.sel = new Set([...S.sel].filter((id) => ids.has(id)));
    S.cur = Math.min(S.cur, items.length - 1);
  }
  S.hasMore = items.length === PAGE_SIZE;
  renderRows();
}

function markRowOpen(id) {
  const it = S.items.find((m) => m.id === id);
  const wasUnread = !!it && !it.is_read;
  if (it && S.openMsg && S.openMsg.id === id && S.openMsg.folder === "inbox" && !S.openMsg.deleted) it.is_read = true;
  if (wasUnread && it.is_read) { renderRows(); return; }   // re-render so the row's hover actions match its new state
  document.querySelectorAll("#rows .row").forEach((row) => row.classList.toggle("sel", Number(row.dataset.id) === id));
}

// ---------------------------------------------------------------- selection + toolbar
function updateRowStates() {
  document.querySelectorAll("#rows .row").forEach((r) => {
    const on = S.sel.has(Number(r.dataset.id));
    r.classList.toggle("selected", on);
    const c = r.querySelector(".chk");
    if (c) c.checked = on;
  });
}
function toggleSel(id, on) {
  if (on) S.sel.add(id); else S.sel.delete(id);
  updateRowStates();
  renderToolbar();
}
function selectAll(on) {
  S.sel = on ? new Set(S.items.map((m) => m.id)) : new Set();
  updateRowStates();
  renderToolbar();
}

function renderToolbar() {
  const n = S.sel.size;
  const all = h("input", { type: "checkbox", class: "chk", id: "selAll", "aria-label": "Select all", onchange: (e) => selectAll(e.target.checked) });
  all.checked = n > 0 && n === S.items.length;
  all.indeterminate = n > 0 && n < S.items.length;
  const kids = [all];
  const ids = () => [...S.sel];
  if (n === 0) {
    kids.push(iconBtn("refresh", "Refresh", refreshAll));
    if (S.view === "trash" && S.items.length) kids.push(h("button", { type: "button", class: "textbtn", onclick: emptyTrash }, "Empty Trash now"));
  } else {
    if (S.view === "trash") {
      kids.push(iconBtn("restore", "Restore", () => bulk("restore", ids())), iconBtn("trash", "Delete forever", () => bulk("delete_forever", ids())));
    } else if (S.view === "drafts") {
      kids.push(iconBtn("trash", "Delete drafts", () => bulk("delete_draft", ids())));
    } else {
      kids.push(
        iconBtn("trash", "Trash", () => bulk("trash", ids())),
        iconBtn("mail-open", "Mark as read", () => bulk("read", ids())),
        iconBtn("mail", "Mark as unread", () => bulk("unread", ids())),
        iconBtn("star", "Star", () => bulk("star", ids())));
    }
    kids.push(h("span", { class: "sel-count" }, `${n} selected`));
  }
  $("#toolbar").replaceChildren(...kids);
}

function refreshAll() {
  refreshCounts();
  loadList();
  if (S.cfg && S.cfg.resend_configured && S.view === "inbox") sync(true);
}

async function emptyTrash() {
  if (!confirm("Permanently delete every message in Trash? This cannot be undone.")) return;
  try {
    const r = await api(`/trash/empty?mailbox=${enc(S.box)}`, { method: "POST" });
    toast(`${r.deleted} message${r.deleted === 1 ? "" : "s"} deleted forever`);
    S.sel = new Set();
    loadList();
  } catch (e) { toast(e.message, true); }
}

// Apply an action to the in-memory list immediately so the UI feels instant.
function applyLocal(action, ids) {
  const set = new Set(ids);
  if (["trash", "restore", "delete_forever", "delete_draft"].includes(action)) {
    S.items = S.items.filter((m) => !set.has(m.id));
    S.cur = Math.min(S.cur, S.items.length - 1);
  } else {
    for (const m of S.items) {
      if (!set.has(m.id)) continue;
      if (action === "read") m.is_read = true;
      if (action === "unread" && m.folder === "inbox") m.is_read = false;
      if (action === "star") m.is_starred = true;
      if (action === "unstar") m.is_starred = false;
    }
  }
  renderRows();
}

async function bulk(action, ids) {
  if (!ids.length) return 0;
  const n = ids.length, plural = n === 1 ? "" : "s";
  if (action === "delete_forever" && !confirm(`Permanently delete ${n} message${plural}? This cannot be undone.`)) return 0;
  if (action === "delete_draft" && !confirm(`Delete ${n} draft${plural}?`)) return 0;
  let changed = 0;
  try {
    if (action === "delete_draft") {
      for (const id of ids) { try { await api(`/drafts/${id}`, { method: "DELETE" }); changed++; } catch {} }
    } else {
      changed = (await api("/messages/bulk", { method: "POST", body: JSON.stringify({ ids, action }) })).updated;
    }
  } catch (e) { toast(e.message, true); return 0; }

  S.sel = new Set();
  if (S.openId && ids.includes(S.openId) && ["trash", "restore", "delete_forever", "unread"].includes(action)) closeReader();
  applyLocal(action, ids);
  refreshCounts();

  if (action === "trash") {
    toast(`${n} message${plural} moved to Trash`, false, { label: "Undo", run: async () => { await bulk("restore", ids); loadList(); } });
  } else if (action === "restore") toast(`${n} message${plural} restored`);
  else if (action === "delete_forever") toast(`${n} message${plural} deleted forever`);
  else if (action === "delete_draft") toast(`${n} draft${plural} deleted`);
  return changed;
}

function setStarLocal(id, on) {
  const it = S.items.find((m) => m.id === id);
  if (it) it.is_starred = on;
  if (S.openMsg && S.openMsg.id === id) S.openMsg.is_starred = on;
  document.querySelectorAll(`#rows .row[data-id="${id}"] .star, #readerStar`).forEach((b) => {
    if (b.id === "readerStar" && !(S.openMsg && S.openMsg.id === id)) return;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
    b.setAttribute("aria-label", on ? "Unstar" : "Star");
  });
}

async function toggleStar(id) {
  const m = S.items.find((x) => x.id === id) || (S.openMsg && S.openMsg.id === id ? S.openMsg : null);
  const on = !(m && m.is_starred);
  setStarLocal(id, on);   // optimistic
  try {
    await api("/messages/bulk", { method: "POST", body: JSON.stringify({ ids: [id], action: on ? "star" : "unstar" }) });
  } catch (e) {
    setStarLocal(id, !on);
    toast(e.message, true);
  }
}

// ---------------------------------------------------------------- reader
const STATUS_CHIP = {
  sent: ["Sent", ""], delivered: ["Delivered", "ok"], bounced: ["Bounced: address rejected the message", "bad"],
  failed: ["Failed to send", "bad"], complained: ["Recipient reported spam", "bad"],
};

function closeReader() {
  S.openId = null;
  S.openMsg = null;
  $("#app").classList.remove("reading");
  markRowOpen(null);
  readerPlaceholder();
}

async function openMessage(id) {
  let m;
  try { m = await api(`/messages/${id}`); } catch (e) { return toast(e.message, true); }
  S.openId = id;
  S.openMsg = m;
  markRowOpen(id);
  $("#app").classList.add("reading");
  if (m.folder === "inbox" && !m.deleted) refreshCounts();   // opening a message marks it read on the server
  renderReader(m);
}

function renderReader(m) {
  const star = h("button", {
    type: "button", id: "readerStar", class: "ib star" + (m.is_starred ? " on" : ""), "aria-pressed": String(!!m.is_starred),
    "aria-label": m.is_starred ? "Unstar" : "Star", title: "Star (S)", onclick: () => toggleStar(m.id)
  }, icon("star"));
  const bar = h("div", { class: "reader-bar" },
    iconBtn("back", "Back to list", closeReader, "back-btn"),
    m.deleted
      ? [iconBtn("restore", "Restore", () => bulk("restore", [m.id])), iconBtn("trash", "Delete forever", () => bulk("delete_forever", [m.id]))]
      : [iconBtn("trash", "Move to Trash (#)", () => bulk("trash", [m.id])),
         m.folder === "inbox" ? iconBtn("mail", "Mark as unread (Shift+U)", () => bulk("unread", [m.id])) : null],
    h("span", { class: "grow" }), star);

  const who = m.from_name || m.from_address;
  const toSummary = (m.to_addrs || []).join(", ") || "(no recipients)";
  const meta = [["From", m.from_name ? `${m.from_name} <${m.from_address}>` : m.from_address], ["To", toSummary]];
  if (m.cc_addrs && m.cc_addrs.length) meta.push(["Cc", m.cc_addrs.join(", ")]);
  meta.push(["Date", fmtShort(m.created_at)]);
  const chip = m.folder === "sent" && STATUS_CHIP[m.status] ? h("span", { class: "chip " + STATUS_CHIP[m.status][1] }, STATUS_CHIP[m.status][0]) : null;

  const head = h("div", { class: "msg-head" },
    h("h1", {}, m.subject || "(no subject)", m.deleted ? h("span", { class: "chip" }, "Trash") : null),
    h("div", { class: "msg-from" },
      avatar(who),
      h("div", { class: "from-text" },
        h("div", {}, h("strong", {}, who), m.from_name ? h("span", { class: "addr" }, ` <${m.from_address}>`) : null),
        h("div", { class: "to-line" }, "to " + toSummary)),
      h("div", { class: "msg-date" }, fmtShort(m.created_at), chip)),
    h("details", { class: "details" }, h("summary", {}, "Details"), h("div", { class: "meta" }, meta.map(([k, v]) => [h("b", {}, k), h("span", {}, v)]))),
    m.folder === "inbox" && !m.deleted ? h("div", { class: "msg-actions" },
      h("button", { type: "button", class: "btn", onclick: () => reply(m) }, icon("reply"), "Reply"),
      hasOthers(m) ? h("button", { type: "button", class: "btn secondary", onclick: () => reply(m, true) }, icon("reply-all"), "Reply all") : null,
      h("button", { type: "button", class: "btn secondary", onclick: () => forward(m) }, icon("forward"), "Forward")) : null);

  const banner = m.deleted ? h("div", { class: "trash-bar", role: "status" },
    h("span", {}, "This message is in Trash."),
    h("button", { type: "button", class: "link", onclick: () => bulk("restore", [m.id]) }, "Restore")) : null;

  let body;
  if (m.folder !== "drafts" && m.html_body && m.folder === "inbox") {
    const hasRemote = REMOTE_IMG_RE.test(m.html_body);
    const frameBody = h("div", { class: "msg-body framed" });
    const renderFrame = (allowImages) => {
      const imgSrc = allowImages ? "https: data:" : "data:";
      const doc = `<!doctype html><meta charset="utf-8"><base target="_blank"><meta name="referrer" content="no-referrer">` +
        `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${imgSrc}; style-src 'unsafe-inline'">` +
        `<style>body{font:14px/1.55 system-ui,sans-serif;margin:16px 28px;color:#222}img{max-width:100%;height:auto}</style>` + m.html_body;
      frameBody.replaceChildren(
        h("iframe", { class: "mail-frame", sandbox: "allow-popups allow-popups-to-escape-sandbox", srcdoc: doc, title: "Message" }));
    };
    renderFrame(false);   // remote images (tracking pixels) are blocked until the user opts in
    if (hasRemote) {
      const imgBar = h("div", { class: "img-bar", role: "status" },
        h("span", {}, "Remote images are blocked to protect your privacy."),
        h("button", { type: "button", class: "link", onclick: () => { renderFrame(true); imgBar.remove(); } }, "Load images"));
      body = h("div", { class: "msg-wrap" }, imgBar, frameBody);
    } else body = frameBody;
  } else {
    body = h("div", { class: "msg-body" }, h("pre", {}, m.text_body || "(empty message)"));
  }
  // banner is null for messages outside Trash; replaceChildren(null) would print the text "null"
  $("#readerPane").replaceChildren(...[bar, head, banner, body].filter(Boolean));
}

// Plain text of a message; HTML-only mail is converted with DOMParser (inert: no scripts run, no resources load).
function plainText(m) {
  if ((m.text_body || "").trim()) return m.text_body.trim();
  if (!m.html_body) return "";
  const doc = new DOMParser().parseFromString(m.html_body, "text/html");
  doc.querySelectorAll("style, script").forEach((n) => n.remove());
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

function quoteOriginal(m) {
  const text = plainText(m);
  if (!text) return "";
  const who = m.from_name ? `${m.from_name} <${m.from_address}>` : m.from_address;
  const quoted = text.split(/\r?\n/).map((l) => "> " + l).join("\n");
  return `\n\nOn ${fmtShort(m.created_at)}, ${who} wrote:\n${quoted}`;
}

const boxInfo = (id) => (S.cfg ? S.cfg.mailboxes.find((m) => m.id === id) : null);

// Everyone on the message except our own mailbox and the sender (who is already the reply's To).
function otherRecipients(m, list) {
  const own = (boxInfo(m.mailbox) || {}).address;
  const skip = new Set([own, m.from_address].filter(Boolean).map((a) => a.toLowerCase()));
  return (list || []).filter((a) => !skip.has(a.toLowerCase()));
}
const hasOthers = (m) => otherRecipients(m, m.to_addrs).length + otherRecipients(m, m.cc_addrs).length > 0;

function reply(m, all = false) {
  const subj = /^re:/i.test(m.subject) ? m.subject : "Re: " + m.subject;
  const p = { from: m.mailbox, to: m.from_address, subject: subj, replyTo: m.id, body: quoteOriginal(m) };
  if (all) {
    const to = [...new Set([m.from_address, ...otherRecipients(m, m.to_addrs)])];
    p.to = to.join(", ");
    p.cc = [...new Set(otherRecipients(m, m.cc_addrs).filter((a) => !to.includes(a)))].join(", ");
  }
  enterCompose();
  renderCompose(p);
}

function forward(m) {
  const subj = /^fwd?:/i.test(m.subject) ? m.subject : "Fwd: " + (m.subject || "");
  const who = m.from_name ? `${m.from_name} <${m.from_address}>` : m.from_address;
  const lines = ["---------- Forwarded message ----------", `From: ${who}`, `Date: ${fmtShort(m.created_at)}`,
                 `Subject: ${m.subject || "(no subject)"}`, `To: ${(m.to_addrs || []).join(", ")}`];
  if (m.cc_addrs && m.cc_addrs.length) lines.push(`Cc: ${m.cc_addrs.join(", ")}`);
  enterCompose();
  // No replyTo: a forward starts a new thread. Attachments are not carried over.
  renderCompose({ from: m.mailbox, subject: subj, body: "\n\n" + lines.join("\n") + "\n\n" + plainText(m) });
}

async function openDraft(id) {
  let m;
  try { m = await api(`/messages/${id}`); } catch (e) { return toast(e.message, true); }
  enterCompose();
  renderCompose({
    from: m.mailbox, to: (m.to_addrs || []).join(", "), cc: (m.cc_addrs || []).join(", "),
    bcc: (m.bcc_addrs || []).join(", "), subject: m.subject || "", body: m.text_body || "", draftId: m.id,
  });
}

// Switch the UI into compose mode (remembering where to return to on Discard).
function enterCompose() {
  const prev = leaveCompose();
  if (S.view !== "compose") S.prevView = S.view;
  S.view = "compose";
  S.openId = null;
  renderNav();
  $("#app").classList.add("composing");
  return prev;
}

// Stop the current compose session. Unsaved edits are saved as a draft first; resolves when that finishes.
function leaveCompose() {
  const c = S.compose;
  S.compose = null;
  if (!c) return Promise.resolve();
  clearTimeout(c.timer);
  if (c.dirty && !c.closed) c.save();
  return c.chain;
}

function renderCompose(p = {}) {
  leaveCompose();
  const from = h("select", { id: "cFrom" }, S.cfg.mailboxes.filter((m) => m.can_send || m.id === p.from).map(
    (m) => h("option", { value: m.id, selected: m.id === (p.from || S.box) }, m.address)));
  const to = h("input", { id: "cTo", value: p.to || "", placeholder: "name@example.com", autocomplete: "off" });
  const cc = h("input", { id: "cCc", value: p.cc || "", placeholder: "optional", autocomplete: "off" });
  const bcc = h("input", { id: "cBcc", value: p.bcc || "", placeholder: "optional", autocomplete: "off" });
  const subject = h("input", { id: "cSubject", value: p.subject || "", placeholder: "Subject" });
  const body = h("textarea", { id: "cBody", placeholder: "Write your message…" });
  const send = h("button", { type: "button", class: "btn", id: "cSend" }, "Send");
  const saveBtn = h("button", { type: "button", class: "btn secondary", id: "cSave" }, "Save draft");
  const discard = h("button", { type: "button", class: "btn secondary", id: "cDiscard" }, p.draftId ? "Delete draft" : "Discard");
  const status = h("span", { class: "hint grow", id: "cStatus", role: "status" }, p.draftId ? "Draft" : "");

  body.value = p.body || "";
  const field = (label, input) => h("div", { class: "field" }, h("label", { for: input.id }, label), input);

  // One object per compose session. Saves are chained so two autosaves can never both create a draft.
  const c = { draftId: p.draftId || null, dirty: false, closed: false, timer: null, chain: Promise.resolve() };
  S.compose = c;
  const hasContent = () => [to, cc, bcc, subject, body].some((el) => el.value.trim());
  const payload = () => ({
    mailbox: from.value, to: to.value, cc: cc.value, bcc: bcc.value,
    subject: subject.value, body: body.value, reply_to_id: p.replyTo || null, draft_id: c.draftId,
  });

  c.save = () => {
    c.chain = c.chain.then(async () => {
      if (c.closed || !c.dirty) return;
      c.dirty = false;
      status.textContent = "Saving…";
      try {
        const r = await api("/drafts", { method: "POST", body: JSON.stringify(payload()) });
        if (r.id) {
          c.draftId = r.id;
          discard.textContent = "Delete draft";
          status.textContent = "Draft saved at " + new Date().toLocaleTimeString([], { timeStyle: "short" });
          refreshCounts();
        } else status.textContent = "";
      } catch (e) {
        c.dirty = true;
        if (/draft not found/i.test(e.message)) c.draftId = null;   // deleted elsewhere: next save recreates it
        status.textContent = "Couldn't save draft";
      }
    });
    return c.chain;
  };
  const touch = () => {
    c.dirty = true;
    clearTimeout(c.timer);
    c.timer = setTimeout(() => c.save(), AUTOSAVE_MS);
  };
  [to, cc, bcc, subject, body].forEach((el) => el.addEventListener("input", touch));
  from.addEventListener("change", touch);

  const doSend = async () => {
    if (send.disabled) return;
    send.disabled = true;
    send.textContent = "Sending…";
    clearTimeout(c.timer);
    await c.chain;            // let an in-flight autosave finish so we know the draft id
    c.closed = true;          // no more autosaves once the send starts
    try {
      await api("/send", {
        method: "POST",
        body: JSON.stringify({ ...payload(), draft_id: c.draftId })
      });
      toast("Message sent");
      S.box = from.value;
      setView("sent");
    } catch (e) {
      c.closed = false;       // sending failed: keep autosaving so nothing is lost
      toast(e.message, true);
      send.disabled = false;
      send.textContent = "Send";
    }
  };
  send.addEventListener("click", doSend);

  saveBtn.addEventListener("click", async () => {
    clearTimeout(c.timer);
    c.dirty = true;
    await c.save();
    toast(c.draftId ? "Draft saved" : "Nothing to save yet");
  });

  discard.addEventListener("click", async () => {
    if (hasContent() && !confirm(c.draftId ? "Delete this draft?" : "Discard this message?")) return;
    clearTimeout(c.timer);
    c.closed = true;
    await c.chain;
    if (c.draftId) { try { await api(`/drafts/${c.draftId}`, { method: "DELETE" }); } catch {} }
    toast(c.draftId ? "Draft deleted" : "Discarded");
    setView(S.prevView === "compose" ? "inbox" : S.prevView);
  });

  // Ctrl/Cmd + Enter sends from anywhere in the compose form.
  const onKey = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); doSend(); } };
  [to, cc, bcc, subject, body].forEach((el) => el.addEventListener("keydown", onKey));

  $("#readerPane").replaceChildren(
    h("div", { class: "compose" },
      h("h2", {}, p.draftId ? "Draft" : p.replyTo ? "Reply" : "New message"),
      field("From", from), field("To", to), field("Cc", cc), field("Bcc", bcc), field("Subject", subject),
      body,
      h("div", { class: "compose-foot" }, send, saveBtn, discard, status),
      h("div", { class: "hint compose-tip" }, "Drafts save automatically. Sent as text + HTML. Ctrl+Enter to send.")
    )
  );
  (p.replyTo ? body : to).focus();
  if (p.replyTo) body.setSelectionRange(0, 0);
}

function setView(view) {
  const wasComposing = S.view === "compose";
  if (view === "compose") {
    enterCompose();
    return renderCompose({ from: S.box });
  }
  const saving = leaveCompose();   // saves unsaved edits as a draft before leaving
  S.view = view;
  S.openId = null; S.openMsg = null;
  S.items = []; S.sel = new Set(); S.cur = -1; S.hasMore = false; S.loading = true;
  renderNav();
  $("#app").classList.remove("composing", "reading");
  $("#listTitle").textContent = TITLES[view] || "Inbox";
  readerPlaceholder();
  renderRows();
  saving.then(() => {
    if (wasComposing) refreshCounts();   // draft counts may have changed
    if (S.view === view) loadList();     // ignore if the user already moved on
  });
}

function selectBox(id) {
  S.box = id;
  renderBoxes();
  setView(S.view === "compose" ? "inbox" : S.view);
}

async function sync(silent = false, full = false) {
  if (S.syncing || !S.cfg) return;   // never overlap two syncs (timer + button click)
  S.syncing = true;
  const btns = [$("#syncBtn"), $("#fullSyncBtn")];
  btns.forEach((b) => (b.disabled = true));
  const label = $("#syncBtn");
  label.textContent = "Syncing…";
  try {
    const r = await api("/sync" + (full ? "?full=1" : ""), { method: "POST" });
    if (r.busy) { if (!silent) toast("A sync is already running"); return; }
    if (r.new) toast(`${r.new} new message${r.new > 1 ? "s" : ""}`);
    else if (!silent) toast(r.failed ? `Inbox synced, but ${r.failed} message(s) could not be downloaded` : "Inbox is up to date");
    if (r.partial && !silent) toast("Sync stopped early to stay within the time limit. Run it again to continue.");
    await refreshCounts();
    if (r.new && S.view === "inbox") loadList();   // don't redraw the list when nothing changed
  } catch (e) {
    if (!silent) toast(e.message, true);
  } finally {
    S.syncing = false;
    btns.forEach((b) => (b.disabled = false));
    label.textContent = "Sync inbox";
  }
}

async function refreshCounts() {
  try { S.counts = await api("/counts"); } catch {}
  try { S.drafts = await api("/counts/drafts"); } catch {}
  if (S.cfg) renderBoxes();
}

function showLogin() {
  stopAutoSync();
  $("#login").hidden = false;
  $("#lEmail").focus();
}

function stopAutoSync() {
  clearInterval(S.timer);
  S.timer = null;
}

function startAutoSync() {
  stopAutoSync();
  S.timer = setInterval(() => { if (!document.hidden) sync(true); }, SYNC_INTERVAL_MS);
}

async function doLogin() {
  const btn = $("#lBtn"), err = $("#lErr");
  err.textContent = "";
  if (!$("#lEmail").value.trim() || !$("#lPass").value) { err.textContent = "Enter your email and password."; return; }
  btn.disabled = true;
  btn.textContent = "Signing in…";
  try {
    const { res, data } = await rawApi("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: $("#lEmail").value, password: $("#lPass").value })
    });
    if (!res.ok) throw new Error(errText(data, res));
    session.set(data);
    $("#lPass").value = "";
    $("#login").hidden = true;
    await start();
  } catch (e) {
    err.textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Sign in";
  }
}

async function signOut() {
  await leaveCompose();   // keep any unsaved work as a draft
  session.clear();
  S.cfg = null; S.counts = {}; S.drafts = {}; S.openId = null; S.openMsg = null; S.q = ""; S.listReq++;
  S.items = []; S.sel = new Set(); S.cur = -1; S.hasMore = false;
  $("#search").value = "";
  $("#rows").replaceChildren();
  $("#toolbar").replaceChildren();
  $("#boxes").replaceChildren();
  $("#who").textContent = "";
  $("#inboxCount").textContent = "";
  $("#draftCount").textContent = "";
  $("#app").classList.remove("composing", "reading");
  readerPlaceholder("");
  showLogin();
}


// ---------------------------------------------------------------- mail lock & archive (administrators)
const LK_ICON = { ok: "✓", warning: "!", fail: "✕" };
const LK_TEXT = {
  ok: "Locked: company mail is held only by this system.",
  warning: "Almost locked: review the warnings below.",
  fail: "Not locked: another mail server can still receive company mail.",
};
function closeLock() { $("#lockPanel").hidden = true; }
function openLock(tab = "lock") {
  const box = $("#lockPanel");
  const body = h("div", {});
  const tabs = h("div", { class: "lk-tabs" },
    h("button", { type: "button", class: tab === "lock" ? "on" : "", onclick: () => openLock("lock") }, "Mail lock"),
    h("button", { type: "button", class: tab === "archive" ? "on" : "", onclick: () => openLock("archive") }, "Archive"));
  box.replaceChildren(h("div", { class: "card", role: "dialog", "aria-modal": "true", "aria-label": "Mail lock and archive" },
    h("div", { class: "card-head" }, h("h2", {}, "Mail lock & archive"), iconBtn("close", "Close", closeLock)), tabs, body));
  box.hidden = false;
  if (tab === "lock") lockTab(body); else archiveTab(body);
}
async function lockTab(body, refresh = false) {
  body.replaceChildren(h("p", {}, "Checking your domain's DNS…"));
  try {
    const r = await api("/compliance" + (refresh ? "?refresh=true" : ""));
    body.replaceChildren(
      h("div", { class: "lk-banner " + r.status }, LK_TEXT[r.status] || r.status),
      r.checks.map((c) => h("div", { class: "lk-check " + c.status },
        h("span", { class: "dot" }, LK_ICON[c.status] || "?"),
        h("div", {}, h("b", {}, c.title), h("div", {}, c.detail)),
        c.fix ? h("div", { class: "fix" }, "How to fix: " + c.fix) : null)),
      h("p", {}, h("button", { type: "button", class: "ghost", onclick: () => lockTab(body, true) }, "Check again")));
  } catch (e) { body.replaceChildren(h("p", {}, e.message)); }
}
function archiveTab(body) {
  const list = h("div", {});
  const detail = h("div", { class: "lk-msg" });
  const q = h("input", { type: "search", placeholder: "Search the archive (sender, subject, text, recipient)", "aria-label": "Search archive" });
  const sel = h("select", { "aria-label": "Mailbox" }, h("option", { value: "" }, "All mailboxes"),
    S.cfg.mailboxes.map((m) => h("option", { value: m.id }, m.id)));
  let t;
  const load = async () => {
    try {
      const rows = await api("/archive?limit=100&q=" + enc(q.value) + "&mailbox=" + enc(sel.value));
      list.replaceChildren(rows.length ? rows.map((m) => h("button", { type: "button", class: "lk-row", onclick: () => show(m.archive_id) },
        h("b", {}, m.subject || "(no subject)"), m.in_mailbox ? null : h("span", { class: "lk-tag" }, "removed from mailbox"),
        h("small", {}, `${m.direction === "inbound" ? "From" : "To/From"} ${m.from_name || m.from_address} · ${m.mailbox_address} · ${fmtShort(m.ts)}`),
        h("small", {}, m.snippet))) : h("p", {}, "Nothing archived matches."));
    } catch (e) { list.replaceChildren(h("p", {}, e.message)); }
  };
  const show = async (id) => {
    try {
      const m = await api("/archive/" + id);
      const names = (a) => a.map((x) => x.email_address).join(", ") || "—";
      let text = m.text_body || "";
      if (!text && m.html_body) text = new DOMParser().parseFromString(m.html_body, "text/html").body.textContent.trim();
      detail.replaceChildren(h("hr", {}), h("dl", {},
        h("dt", {}, "From"), h("dd", {}, `${m.from_name ? m.from_name + " " : ""}<${m.from_address}>`),
        h("dt", {}, "To"), h("dd", {}, names(m.to_addrs)), h("dt", {}, "Cc"), h("dd", {}, names(m.cc_addrs)),
        m.bcc_addrs.length ? [h("dt", {}, "Bcc"), h("dd", {}, names(m.bcc_addrs))] : null,
        h("dt", {}, "Subject"), h("dd", {}, m.subject || "(no subject)"),
        h("dt", {}, "Date"), h("dd", {}, fmtShort(m.received_at || m.sent_at || m.archived_at)),
        h("dt", {}, "Seal"), h("dd", {}, "SHA-256 " + m.content_sha256.slice(0, 16) + "…")),
        h("pre", {}, text || "(no text content)"));
      detail.scrollIntoView({ block: "nearest" });
    } catch (e) { detail.replaceChildren(h("p", {}, e.message)); }
  };
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  sel.addEventListener("change", load);
  body.replaceChildren(h("p", {}, "Every message ever received or sent is kept here permanently, even after it is deleted from a mailbox."),
    h("div", { class: "lk-bar" }, q, sel), list, detail);
  load();
}

async function start() {
  try { S.cfg = await api("/config"); }
  catch (e) { session.clear(); showLogin(); $("#lErr").textContent = e.message; return; }

  if (!S.cfg.mailboxes.some((m) => m.id === S.box)) S.box = S.cfg.mailboxes[0]?.id || "admin";
  $("#who").textContent = S.cfg.user;
  $("#lockBtn").hidden = !S.cfg.is_admin;
  const st = $("#status");
  st.textContent = S.cfg.resend_configured ? "Resend connected" : "RESEND_API_KEY is not set on the server.";
  st.className = "status" + (S.cfg.resend_configured ? "" : " warn");
  await refreshCounts();
  setView("inbox");
  if (S.cfg.resend_configured) {
    sync(true);
    startAutoSync();
  }
}

// ---------------------------------------------------------------- keyboard shortcuts
const SHORTCUTS = [
  ["Compose", "C"], ["Search", "/"], ["Next / previous message", "J / K"], ["Open message", "Enter or O"],
  ["Select message", "X"], ["Star / unstar", "S"], ["Move to Trash", "# or Delete"],
  ["Mark as read / unread", "Shift+I / Shift+U"], ["Reply / Reply all / Forward", "R / A / F"],
  ["Back to list, clear selection", "U or Esc"], ["Go to Inbox / Starred / Sent / Drafts / Trash", "G then I / S / T / D / B"],
  ["Show this help", "?"],
];

function toggleHelp(show) {
  const box = $("#help");
  if (show === false || (show === undefined && !box.hidden)) { box.hidden = true; return; }
  box.replaceChildren(h("div", { class: "card", role: "dialog", "aria-modal": "true", "aria-label": "Keyboard shortcuts" },
    h("div", { class: "card-head" }, h("h2", {}, "Keyboard shortcuts"), iconBtn("close", "Close", () => toggleHelp(false))),
    h("table", {}, SHORTCUTS.map(([what, keys]) => h("tr", {}, h("td", {}, what), h("td", {}, h("kbd", {}, keys)))))));
  box.hidden = false;
}

function moveCur(d) {
  if (!S.items.length) return;
  S.cur = Math.max(0, Math.min(S.items.length - 1, (S.cur < 0 ? (d > 0 ? -1 : S.items.length) : S.cur) + d));
  $("#rows").classList.add("kbd");   // show the cursor outline only while the keyboard is in use
  document.querySelectorAll("#rows .row").forEach((r, i) => r.classList.toggle("cur", i === S.cur));
  const el = document.querySelectorAll("#rows .row")[S.cur];
  if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
}

function targetIds() {
  if (S.sel.size) return [...S.sel];
  if (S.openId) return [S.openId];
  const m = S.items[S.cur];
  return m ? [m.id] : [];
}

let gPending = false, gTimer;
function onKey(e) {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target, tag = t && t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) {
    if (e.key === "Escape" && t.id === "search") t.blur();
    return;
  }
  if (!S.cfg || !$("#login").hidden) return;
  if (!$("#help").hidden) { if (e.key === "Escape" || e.key === "?") toggleHelp(false); return; }
  if (!$("#lockPanel").hidden) { if (e.key === "Escape") closeLock(); return; }
  const k = e.key;
  if (gPending) {
    gPending = false;
    clearTimeout(gTimer);
    const v = { i: "inbox", s: "starred", t: "sent", d: "drafts", b: "trash" }[k.toLowerCase()];
    if (v) { e.preventDefault(); setView(v); }
    return;
  }
  if (S.view === "compose") return;   // the compose form has its own controls
  const open = S.openMsg;
  switch (k) {
    case "?": toggleHelp(true); break;
    case "g": gPending = true; gTimer = setTimeout(() => (gPending = false), 1200); break;
    case "c": e.preventDefault(); setView("compose"); break;
    case "/": e.preventDefault(); $("#search").focus(); break;
    case "j": moveCur(1); break;
    case "k": moveCur(-1); break;
    case "Enter": if (t === document.body && S.items[S.cur]) { const m = S.items[S.cur]; (S.view === "drafts" ? openDraft : openMessage)(m.id); } break;
    case "o": if (S.items[S.cur]) { const m = S.items[S.cur]; (S.view === "drafts" ? openDraft : openMessage)(m.id); } break;
    case "x": if (S.items[S.cur]) toggleSel(S.items[S.cur].id, !S.sel.has(S.items[S.cur].id)); break;
    case "s": { const id = S.openId || (S.items[S.cur] && S.items[S.cur].id); if (id && S.view !== "drafts") toggleStar(id); break; }
    case "#": case "Delete": bulk(S.view === "trash" ? "delete_forever" : S.view === "drafts" ? "delete_draft" : "trash", targetIds()); break;
    case "I": if (S.view !== "trash" && S.view !== "drafts") bulk("read", targetIds()); break;
    case "U": if (S.view !== "trash" && S.view !== "drafts") bulk("unread", targetIds()); break;
    case "u": if (S.openId) closeReader(); break;
    case "r": if (open && open.folder === "inbox" && !open.deleted) reply(open); break;
    case "a": if (open && open.folder === "inbox" && !open.deleted && hasOthers(open)) reply(open, true); break;
    case "f": if (open && open.folder === "inbox" && !open.deleted) forward(open); break;
    case "Escape": if (S.sel.size) selectAll(false); else if (S.openId) closeReader(); break;
  }
}

(function init() {
  applyCompanyBrand();
  applyDbTheme();
  document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = svg(el.dataset.icon); });
  $("#composeBtn").addEventListener("click", () => S.cfg && setView("compose"));
  document.querySelectorAll(".nav button").forEach((b) => b.addEventListener("click", () => S.cfg && setView(b.dataset.view)));
  $("#syncBtn").addEventListener("click", () => sync(false));
  $("#fullSyncBtn").addEventListener("click", () => sync(false, true));
  $("#signOutBtn").addEventListener("click", signOut);
  $("#helpBtn").addEventListener("click", () => toggleHelp(true));
  $("#help").addEventListener("click", (e) => { if (e.target.id === "help") toggleHelp(false); });
  $("#lockPanel").addEventListener("click", (e) => { if (e.target.id === "lockPanel") closeLock(); });
  $("#lockBtn").addEventListener("click", () => openLock("lock"));
  $("#lBtn").addEventListener("click", doLogin);
  $("#lPass").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
  $("#lEmail").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#lPass").focus(); });
  document.addEventListener("keydown", onKey);
  document.addEventListener("mousedown", () => $("#rows").classList.remove("kbd"));
  let t;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { S.q = e.target.value; if (S.cfg && S.view !== "compose") { S.sel = new Set(); loadList(); } }, 250);
  });
  // Catch up immediately when the tab becomes visible again after being in the background.
  document.addEventListener("visibilitychange", () => { if (!document.hidden && S.cfg && S.timer) sync(true); });
  // Never lose typing: warn on tab close with unsaved edits, and save when the tab is hidden.
  window.addEventListener("beforeunload", (e) => {
    if (S.compose && S.compose.dirty && !S.compose.closed) { e.preventDefault(); e.returnValue = ""; }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && S.compose && S.compose.dirty && !S.compose.closed) S.compose.save();
  });
  readerPlaceholder();
  renderToolbar();
  if (session.get()) start(); else showLogin();
})();
