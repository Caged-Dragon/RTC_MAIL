// Browser-logic smoke test: runs the real public/app.js inside jsdom against an in-memory fake of the API.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const root = require("path").join(__dirname, "..", "..", "public") + "/";

// ------------------------------------------------------------------ fake API (mirrors the real contracts)
const db = { drafts: [], sent: [], nextId: 1000, calls: [] };
const mk = (id, mailbox, subject, extra = {}) => ({
  id, mailbox, folder: "inbox", subject, from_name: "Jane", from_address: "jane@example.com",
  to_addrs: [mailbox + "@rtcrackers.com"], cc_addrs: [], bcc_addrs: [], text_body: "Body of " + subject, html_body: null,
  snippet: "snippet " + subject, is_read: false, is_starred: false, deleted: false, status: "received",
  message_id: `<${id}@x>`, created_at: new Date(Date.now() - id * 1000).toISOString(), ...extra,
});
const MAIL = [
  mk(500, "sales", "Order question", { from_name: "Jane", to_addrs: ["sales@rtcrackers.com", "bob@example.com"],
    cc_addrs: ["Carol@example.com", "jane@example.com"], text_body: "Hello\nthere", is_read: true }),
  mk(501, "sales", "Fwd: Html only", { from_name: "", from_address: "solo@example.com", text_body: "",
    html_body: "<style>p{color:red}</style><p>Rich <b>body</b></p>", is_read: true }),
  mk(502, "sales", "Alpha"), mk(503, "sales", "Beta"), mk(504, "sales", "Gamma"),
];
for (let i = 0; i < 120; i++) MAIL.push(mk(2000 + i, "info", "Bulk " + i, { is_read: true }));

const cfg = { domain: "x", user: "admin@rtcrackers.com", resend_configured: true,
  mailboxes: ["admin", "sales", "info"].map((id) => ({ id, label: id, address: id + "@rtcrackers.com", can_send: true })) };
const json = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const asList = (m) => ({ ...m, to_addrs: m.to_addrs || [] });

function listFor(q) {
  const mailbox = q.get("mailbox"), folder = q.get("folder"), text = (q.get("q") || "").toLowerCase();
  let rows;
  if (folder === "drafts") rows = db.drafts.filter((d) => d.mailbox === mailbox).map((d) => ({ ...mk(d.id, mailbox, d.subject), ...d, folder: "drafts",
    to_addrs: d.to ? d.to.split(",").map((x) => x.trim()).filter(Boolean) : [] }));
  else if (folder === "sent") rows = db.sent.map((d) => ({ ...mk(d.id, mailbox, d.subject), folder: "sent" }));
  else {
    rows = MAIL.filter((m) => m.mailbox === mailbox);
    rows = folder === "trash" ? rows.filter((m) => m.deleted)
         : folder === "starred" ? rows.filter((m) => m.is_starred && !m.deleted)
         : rows.filter((m) => !m.deleted);
  }
  if (text.includes("is:unread")) rows = rows.filter((m) => !m.is_read);
  else if (text) rows = rows.filter((m) => (m.subject || "").toLowerCase().includes(text));
  rows = rows.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  const off = +q.get("offset") || 0, lim = +q.get("limit") || 50;
  return rows.slice(off, off + lim).map(asList);
}

function pulseData() {
  const counts = {}, latest = {}, drafts = {};
  MAIL.filter((m) => !m.deleted).forEach((m) => {
    if (!m.is_read) counts[m.mailbox] = (counts[m.mailbox] || 0) + 1;
    latest[m.mailbox] = Math.max(latest[m.mailbox] || 0, m.id);
  });
  db.drafts.forEach((d) => (drafts[d.mailbox] = (drafts[d.mailbox] || 0) + 1));
  return { counts, drafts, latest };
}

async function fakeFetch(url, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const body = opts.body ? JSON.parse(opts.body) : null;
  db.calls.push(method + " " + url);
  if (url === "/api/company" || url === "/api/theme") return json({});
  if (url === "/api/branding") return json({ theme: { components: [] }, company: {} });
  if (url === "/api/config") return json(cfg);
  if (url === "/api/pulse") return json(pulseData());
  if (url.startsWith("/api/bootstrap?")) {
    const q = new URL("http://x" + url.slice(4)).searchParams;
    const box = cfg.mailboxes.some((m) => m.id === q.get("mailbox")) ? q.get("mailbox") : cfg.mailboxes[0].id;
    const items = listFor(new URLSearchParams({ mailbox: box, folder: q.get("folder") || "inbox", limit: "50", offset: "0", q: "" }));
    return json({ ...cfg, user: cfg.user, mailbox: box, folder: q.get("folder") || "inbox", webhook_configured: true, ...pulseData(), items });
  }
  if (url === "/api/sync" && method === "POST") return json({ new: 0, failed: 0, partial: false });
  if (url === "/api/counts") {
    const o = {}; MAIL.filter((m) => !m.is_read && !m.deleted).forEach((m) => (o[m.mailbox] = (o[m.mailbox] || 0) + 1)); return json(o);
  }
  if (url === "/api/counts/drafts") { const o = {}; db.drafts.forEach((d) => (o[d.mailbox] = (o[d.mailbox] || 0) + 1)); return json(o); }
  if (url.startsWith("/api/messages?")) return json(listFor(new URL("http://x" + url.slice(4)).searchParams));
  let m;
  if (url === "/api/messages/bulk" && method === "POST") {
    let n = 0;
    for (const id of body.ids) {
      const i = MAIL.findIndex((x) => x.id === id); if (i < 0) continue;
      const x = MAIL[i];
      if (body.action === "star" && !x.is_starred) { x.is_starred = true; n++; }
      if (body.action === "unstar" && x.is_starred) { x.is_starred = false; n++; }
      if (body.action === "read" && !x.is_read) { x.is_read = true; n++; }
      if (body.action === "unread" && x.is_read) { x.is_read = false; n++; }
      if (body.action === "trash" && !x.deleted) { x.deleted = true; n++; }
      if (body.action === "restore" && x.deleted) { x.deleted = false; n++; }
      if (body.action === "delete_forever" && x.deleted) { MAIL.splice(i, 1); n++; }
    }
    return json({ updated: n });
  }
  if (url.startsWith("/api/trash/empty") && method === "POST") {
    const mailbox = new URL("http://x" + url.slice(4)).searchParams.get("mailbox");
    let n = 0; for (let i = MAIL.length - 1; i >= 0; i--) if (MAIL[i].mailbox === mailbox && MAIL[i].deleted) { MAIL.splice(i, 1); n++; }
    return json({ deleted: n });
  }
  if ((m = url.match(/^\/api\/messages\/(\d+)$/))) {
    const x = MAIL.find((y) => y.id === +m[1]);
    if (x) { if (!x.deleted) x.is_read = true; return json({ ...x }); }
    const d = db.drafts.find((y) => y.id === +m[1]); if (!d) return json({ detail: "Message not found" }, 404);
    return json({ ...d, folder: "drafts", text_body: d.body, to_addrs: d.to ? d.to.split(",").map((s) => s.trim()) : [], cc_addrs: [], bcc_addrs: [], created_at: new Date().toISOString() });
  }
  if (url === "/api/drafts" && method === "POST") {
    const empty = !(body.to || body.subject || body.body.trim());
    if (!body.draft_id && empty) return json({ id: null, saved_at: null });
    let d = body.draft_id && db.drafts.find((x) => x.id === body.draft_id);
    if (body.draft_id && !d) return json({ detail: "Draft not found" }, 404);
    if (!d) { d = { id: db.nextId++ }; db.drafts.push(d); }
    Object.assign(d, { mailbox: body.mailbox, to: body.to, subject: body.subject, body: body.body });
    return json({ id: d.id, saved_at: new Date().toISOString() });
  }
  if ((m = url.match(/^\/api\/drafts\/(\d+)$/)) && method === "DELETE") {
    const i = db.drafts.findIndex((x) => x.id === +m[1]); if (i < 0) return json({ detail: "Draft not found" }, 404);
    db.drafts.splice(i, 1); return json({ deleted: true });
  }
  if (url === "/api/send" && method === "POST") {
    if (body.draft_id) { const i = db.drafts.findIndex((x) => x.id === body.draft_id); if (i >= 0) db.drafts.splice(i, 1); }
    db.sent.push({ id: db.nextId++, subject: body.subject, to: body.to }); return json({ id: 1 });
  }
  return json({ detail: "unhandled " + url }, 500);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (name, cond) => { console.log((cond ? "PASS " : "FAIL ") + name); if (!cond) failures++; };

(async () => {
  const html = fs.readFileSync(root + "index.html", "utf8").replace(/<script src="\/app.js"><\/script>/, "").replace(/<link[^>]*>/, "");
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost/", pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = fakeFetch;
  w.localStorage.setItem("rtmail.session", JSON.stringify({ access_token: "a", refresh_token: "r" }));
  w.confirm = () => true;
  w.eval(fs.readFileSync(root + "app.js", "utf8"));
  const $ = (s) => w.document.querySelector(s);
  const $$ = (s) => [...w.document.querySelectorAll(s)];
  const type = (sel, v) => { const el = $(sel); el.value = v; el.dispatchEvent(new w.Event("input", { bubbles: true })); };
  const click = (sel) => $(sel).dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const clickEl = (el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const key = (k, extra = {}) => w.document.body.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true, ...extra }));
  const row = (id) => $(`#rows .row[data-id="${id}"]`);
  const ids = () => $$("#rows .row").map((r) => +r.dataset.id);
  const tbBtn = (label) => $$("#toolbar button").find((b) => b.getAttribute("aria-label") === label);
  const gotoBox = async (id) => { clickEl($$("#boxes .box").find((b) => b.textContent.startsWith(id))); await sleep(150); };
  const badge = (id) => { const b = $$("#boxes .box").find((x) => x.textContent.startsWith(id)); const n = b && b.querySelector(".badge"); return n ? +n.textContent : 0; };
  const gotoView = async (v) => { click(`[data-view="${v}"]`); await sleep(150); };

  await sleep(150);
  const bootCalls = db.calls.slice();
  check("start-up needs exactly ONE data request (bootstrap) - no config/counts/list round trips",
    bootCalls.filter((c) => c.includes("/api/bootstrap")).length === 1 &&
    !bootCalls.some((c) => /\/api\/(config|counts|messages\?)/.test(c)));
  check("skeleton rows are replaced by the real list as soon as the data arrives", $$("#rows .skel").length === 0 && $("#rows").children.length > 0);
  check("branding is requested once, after the first screen (not in parallel with the data)",
    bootCalls.filter((c) => c.includes("/api/branding")).length === 1 && bootCalls.findIndex((c) => c.includes("/api/branding")) > bootCalls.findIndex((c) => c.includes("/api/bootstrap")));
  check("no sync is fired during start-up (it is deferred until the screen is usable)", !bootCalls.some((c) => c.includes("/api/sync")));
  check("app boots with the five folders, each with an icon",
    $$(".nav button").length === 5 && $$(".nav button .ic svg").length === 5 && $("#boxes").children.length === 3);

  // ---------------------------------------------------------------- drafts (autosave, resume, send, discard)
  click("#composeBtn"); await sleep(20);
  type("#cTo", "bob@example.com"); type("#cSubject", "Quote");
  await sleep(1700);
  type("#cBody", "first text"); await sleep(1700);
  check("autosave created exactly one draft", db.drafts.length === 1 && db.drafts[0].body === "first text");
  check("status shows saved + button says Delete draft", /saved/i.test($("#cStatus").textContent) && $("#cDiscard").textContent === "Delete draft");
  type("#cBody", "edited then navigated away");
  await gotoView("drafts");
  check("edits saved when navigating away", db.drafts[0].body === "edited then navigated away");
  check("drafts list shows the draft with tag + badge", !!$("#rows .row .draft-tag") && $("#draftCount").textContent === "1");
  clickEl($("#rows .row-main")); await sleep(100);
  check("draft reopened with its content", $("#cBody").value === "edited then navigated away" && $("#cTo").value === "bob@example.com");
  click("#cSend"); await sleep(200);
  check("send consumed draft and lands in Sent", db.drafts.length === 0 && db.sent.length === 1 && $("#listTitle").textContent === "Sent");
  click("#composeBtn"); await sleep(20);
  type("#cTo", "a@example.com"); type("#cSubject", "Race"); type("#cBody", "x");
  await sleep(1550); click("#cSend"); await sleep(2300);
  check("no orphan draft after fast send", db.drafts.length === 0 && db.sent.length === 2);
  click("#composeBtn"); await sleep(20); click("#cDiscard"); await sleep(150);
  check("discarding an empty compose creates nothing", db.drafts.length === 0);

  // ---------------------------------------------------------------- reply all / forward
  await gotoView("inbox"); await gotoBox("sales");
  const open = async (id) => { clickEl(row(id).querySelector(".row-main")); await sleep(120); };
  const act = (label) => $$(".msg-actions button").find((b) => b.textContent === label);
  await open(500);
  check("Reply, Reply all, Forward buttons shown", !!act("Reply") && !!act("Reply all") && !!act("Forward"));
  act("Reply all").click(); await sleep(50);
  check("reply-all fills To/Cc correctly and quotes", $("#cTo").value === "jane@example.com, bob@example.com" && $("#cCc").value === "Carol@example.com" && $("#cBody").value.includes("> Hello\n> there"));
  click("#cDiscard"); await sleep(100);
  await gotoView("inbox"); await open(500);
  act("Forward").click(); await sleep(50);
  check("forward: empty To, Fwd: subject, header block", $("#cTo").value === "" && $("#cSubject").value === "Fwd: Order question" && /Forwarded message/.test($("#cBody").value));
  click("#cDiscard"); await sleep(100);
  await gotoView("inbox"); await open(501);
  act("Forward").click(); await sleep(50);
  check("HTML-only mail forwards as clean plain text", $("#cBody").value.includes("Rich body") && !/[<>]|color:red/.test($("#cBody").value));
  click("#cDiscard"); await sleep(100);

  // ---------------------------------------------------------------- stars, read state, mobile back
  await gotoView("inbox");
  check("inbox lists sales mail newest first", ids().slice(0, 5).join() === "500,501,502,503,504");
  check("unread rows are marked unread", row(502).classList.contains("unread") && !row(500).classList.contains("unread"));
  clickEl(row(502).querySelector(".star")); await sleep(80);
  check("starring updates the row immediately", row(502).querySelector(".star").classList.contains("on") && MAIL.find((m) => m.id === 502).is_starred);
  await gotoView("starred");
  check("Starred folder lists only starred mail", ids().join() === "502");
  await gotoView("inbox");
  const unreadBefore = badge("sales"), callsBefore = db.calls.length;
  await open(502);
  const openCalls = db.calls.slice(callsBefore).filter((c) => !c.includes("/api/pulse"));
  check("opening a message costs ONE request and lowers the unread badge locally",
    openCalls.length === 1 && openCalls[0] === "GET /api/messages/502" && badge("sales") === unreadBefore - 1);
  check("opening a message marks it read in the list + shows reading pane class",
    !row(502).classList.contains("unread") && $("#app").classList.contains("reading") && $("#readerStar").classList.contains("on"));
  check("reader never prints a stray 'null' (missing banner regression)", !/null/.test($("#readerPane").textContent));
  clickEl($(".back-btn")); await sleep(30);
  check("Back button returns to the list (mobile flow)", !$("#app").classList.contains("reading") && /Select a message/.test($("#readerPane").textContent));
  clickEl(row(502).querySelector('.row-actions button[aria-label="Mark as unread"]')); await sleep(100);
  check("hover action marks as unread", row(502).classList.contains("unread") && !MAIL.find((m) => m.id === 502).is_read);

  // ---------------------------------------------------------------- bulk select, trash, undo
  row(503).querySelector(".chk").click(); row(504).querySelector(".chk").click(); await sleep(30);
  check("selecting rows shows the bulk toolbar", /2 selected/.test($("#toolbar").textContent) && !!tbBtn("Trash") && !!tbBtn("Mark as unread") && !!tbBtn("Star"));
  const pulsesBefore = db.calls.filter((c) => c.includes("/api/pulse")).length;
  clickEl(tbBtn("Trash")); await sleep(120);
  check("bulk trash removes rows and shows an Undo toast",
    !ids().includes(503) && !ids().includes(504) && /moved to Trash/.test($("#toast").textContent) && !!$("#toast button"));
  await sleep(900);
  check("trashing several messages triggers a single counts refresh, not one per message",
    db.calls.filter((c) => c.includes("/api/pulse")).length === pulsesBefore + 1);
  clickEl($("#toast button")); await sleep(250);
  check("Undo restores both messages", ids().includes(503) && ids().includes(504) && !MAIL.find((m) => m.id === 503).deleted);

  // ---------------------------------------------------------------- trash folder: view, restore, delete forever, empty
  clickEl(row(504).querySelector('.row-actions button[aria-label="Trash"]')); await sleep(100);
  await gotoView("trash");
  check("Trash folder lists the message", ids().join() === "504");
  await open(504);
  check("trashed message shows banner and no reply buttons", !!$(".trash-bar") && !act("Reply") && !MAIL.find((m) => m.id === 504).is_read);
  clickEl($$(".reader-bar button").find((b) => b.getAttribute("aria-label") === "Restore")); await sleep(150);
  check("Restore from the reader returns it to the inbox", MAIL.find((m) => m.id === 504).deleted === false && ids().length === 0);
  clickEl(row ? (await (async () => { await gotoView("inbox"); return row(504).querySelector('.row-actions button[aria-label="Trash"]'); })()) : null); await sleep(100);
  await gotoView("trash");
  clickEl(row(504).querySelector('.row-actions button[aria-label="Delete forever"]')); await sleep(120);
  check("Delete forever removes it for good", !MAIL.some((m) => m.id === 504) && /Trash is empty/.test($("#rows").textContent));
  await gotoView("inbox");
  clickEl(row(503).querySelector('.row-actions button[aria-label="Trash"]')); await sleep(100);
  await gotoView("trash");
  clickEl($$("#toolbar button").find((b) => /Empty Trash/.test(b.textContent))); await sleep(150);
  check("Empty Trash now clears the folder", !MAIL.some((m) => m.id === 503) && /Trash is empty/.test($("#rows").textContent));

  // ---------------------------------------------------------------- pagination
  await gotoView("inbox"); await gotoBox("info");
  check("first page has 50 rows and a Load more button", ids().length === 50 && !!$("#rows .more"));
  clickEl($("#rows .more")); await sleep(120);
  check("Load more appends the next page", ids().length === 100 && !!$("#rows .more"));
  clickEl($("#rows .more")); await sleep(120);
  check("last page loads everything and removes the button", ids().length === 120 && !$("#rows .more") && new Set(ids()).size === 120);

  // ---------------------------------------------------------------- search
  await gotoBox("sales");
  type("#search", "is:unread"); await sleep(500);
  check("search sends the query and lists only matches", db.calls.some((c) => c.includes("q=is%3Aunread")) && ids().join() === "502");
  type("#search", ""); await sleep(500);

  // ---------------------------------------------------------------- keyboard shortcuts
  await gotoView("inbox");
  check("cursor outline is hidden until the keyboard is used", !$("#rows").classList.contains("kbd"));
  key("j"); await sleep(20);
  check("J moves the keyboard cursor to the first row", row(500).classList.contains("cur") && $("#rows").classList.contains("kbd"));
  key("j"); key("k"); await sleep(20);
  check("K moves it back up", row(500).classList.contains("cur") && !row(501).classList.contains("cur"));
  key("s"); await sleep(100);
  check("S stars the current message", row(500).querySelector(".star").classList.contains("on") && MAIL.find((m) => m.id === 500).is_starred);
  key("#"); await sleep(120);
  check("# trashes the current message", !ids().includes(500) && MAIL.find((m) => m.id === 500).deleted);
  key("?"); await sleep(20);
  check("? opens the shortcut help", !$("#help").hidden && /Keyboard shortcuts/.test($("#help").textContent));
  key("Escape"); await sleep(20);
  check("Escape closes the help", $("#help").hidden);
  key("g"); key("s"); await sleep(150);
  check("G then S goes to Starred", $("#listTitle").textContent === "Starred");
  key("g"); key("i"); await sleep(150);
  key("c"); await sleep(30);
  check("C opens compose", !!$("#cTo") && $("#app").classList.contains("composing"));
  key("j");  // typing in compose must never trigger list shortcuts
  check("shortcuts are ignored while composing", !!$("#cTo"));
  click("#cDiscard"); await sleep(100);

  // ---------------------------------------------------------------- new mail arrives (stored by the webhook) -> pulse shows it
  await gotoView("inbox"); await gotoBox("sales");
  await sleep(1000);                       // let the previous action's coalesced counts refresh finish first
  const beforeNew = badge("sales");
  MAIL.push(mk(9000, "sales", "Fresh arrival", { created_at: new Date().toISOString() }));
  const callsBeforeNew = db.calls.length;
  w.document.dispatchEvent(new w.Event("visibilitychange")); await sleep(400);
  const newMailCalls = db.calls.slice(callsBeforeNew).map((c) => c.split("?")[0]);
  check("new mail appears in the open inbox without a manual sync", ids()[0] === 9000 && /New mail/.test($("#toast").textContent));
  check("the unread badge rises by one", badge("sales") === beforeNew + 1);
  check("finding new mail costs exactly two requests: one pulse + one list reload (no sync, no counts calls)",
    newMailCalls.join() === "GET /api/pulse,GET /api/messages");

  // ---------------------------------------------------------------- the last-used mailbox is remembered
  check("the chosen mailbox is remembered for next time", w.localStorage.getItem("rtmail.box") === "sales");

  check("no unhandled API calls or leftover drafts", !db.calls.some((c) => c.includes("unhandled")) && db.drafts.length === 0);
  console.log(failures ? `\n${failures} FAILED` : "\nALL FRONTEND CHECKS PASSED");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
