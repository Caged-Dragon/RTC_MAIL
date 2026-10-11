"""Service-layer tests against the scratch Postgres schema."""
import pytest

from mailcore import resend_client, service
from mailcore.auth import User

ADMIN = User("dev", "dev@local", True)


def received(rid="r1", to="sales@rtcrackers.com", **extra):
    return {"id": rid, "to": [to], **extra}


@pytest.fixture
def fake_resend(monkeypatch):
    sent, fetched = [], []

    def get_received(email_id):
        fetched.append(email_id)
        return {"from": "Jane <jane@example.com>", "subject": "Hello", "text": "Body text",
                "html": "<p>Body</p>", "created_at": "2025-03-04 05:06:07+00", "message_id": f"<{email_id}@x>"}

    monkeypatch.setattr(resend_client, "get_received", get_received)
    monkeypatch.setattr(resend_client, "send", lambda p: sent.append(p) or {"id": f"out-{len(sent)}"})
    return type("F", (), {"sent": sent, "fetched": fetched})


def test_ensure_mailboxes_creates_all_required(db):
    service.ensure_mailboxes()
    service.ensure_mailboxes()  # idempotent
    locals_ = [b["local_part"] for b in service.list_mailboxes(ADMIN)]
    assert locals_ == ["admin", "sales", "support", "account", "noreply", "billing", "contact", "help", "info", "orders"]


def test_ingest_stores_message_and_is_idempotent(db, fake_resend):
    service.ensure_mailboxes()
    assert service.ingest_received(received())["new"] == 1
    again = service.ingest_received(received())
    assert again["new"] == 0 and again["known"] is True
    assert fake_resend.fetched == ["r1"]  # second pass did not re-download

    [row] = service.list_messages(ADMIN, "sales", "inbox")
    assert row["from_address"] == "jane@example.com" and row["from_name"] == "Jane" and not row["is_read"]


def test_ingest_ignores_mail_for_other_domains(db, fake_resend):
    service.ensure_mailboxes()
    r = service.ingest_received(received(to="someone@elsewhere.com"))
    assert r["ours"] is False and r["new"] == 0 and fake_resend.fetched == []


def test_same_email_to_two_mailboxes_is_stored_for_each(db, fake_resend):
    service.ensure_mailboxes()
    r = service.ingest_received({"id": "r9", "to": ["sales@rtcrackers.com"], "cc": ["support@rtcrackers.com"]})
    assert r["new"] == 2
    assert len(service.list_messages(ADMIN, "sales", "inbox")) == 1
    assert len(service.list_messages(ADMIN, "support", "inbox")) == 1


def test_download_failure_is_counted_not_raised(db, monkeypatch):
    service.ensure_mailboxes()
    monkeypatch.setattr(resend_client, "get_received",
                        lambda i: (_ for _ in ()).throw(resend_client.ResendError(404, "gone")))
    r = service.ingest_received(received())
    assert r["failed"] == 1 and r["new"] == 0


def test_config_errors_still_propagate(db, monkeypatch):
    service.ensure_mailboxes()
    monkeypatch.setattr(resend_client, "get_received",
                        lambda i: (_ for _ in ()).throw(resend_client.ResendError(401, "bad key")))
    with pytest.raises(resend_client.ResendError):
        service.ingest_received(received())


def test_get_message_marks_read_and_reports_it(db, fake_resend):
    service.ensure_mailboxes()
    service.ingest_received(received())
    mid = service.list_messages(ADMIN, "sales", "inbox")[0]["id"]
    assert service.counts(ADMIN) == {"sales": 1}
    msg = service.get_message(ADMIN, mid)
    assert msg["is_read"] is True and msg["to_addrs"] == ["sales@rtcrackers.com"]
    assert service.counts(ADMIN) == {}


def test_search_filters_and_escapes_wildcards(db, fake_resend):
    service.ensure_mailboxes()
    service.ingest_received(received())
    assert len(service.list_messages(ADMIN, "sales", "inbox", "hello")) == 1
    assert service.list_messages(ADMIN, "sales", "inbox", "nomatch") == []
    assert service.list_messages(ADMIN, "sales", "inbox", "%") == []  # literal %, not a wildcard


def test_non_admin_only_sees_granted_mailboxes(db, fake_resend):
    service.ensure_mailboxes()
    service.ingest_received(received())
    sales_id = next(b["mailbox_id"] for b in service.list_mailboxes(ADMIN) if b["local_part"] == "sales")
    scoped = User("u", "u@x", False, {sales_id})
    assert [b["local_part"] for b in service.list_mailboxes(scoped)] == ["sales"]
    with pytest.raises(LookupError):
        service.list_messages(scoped, "admin", "inbox")
    mid = service.list_messages(ADMIN, "sales", "inbox")[0]["id"]
    assert service.get_message(User("u", "u@x", False, set()), mid) is None


def test_send_mail_builds_payload_and_stores_sent_copy(db, fake_resend):
    service.ensure_mailboxes()
    mid = service.send_mail(ADMIN, "sales", "Bob <bob@example.com>, bob@example.com", "c@example.com", "",
                            "Hi\r\nthere\nX-Evil: 1", "line1\nline2")
    p = fake_resend.sent[0]
    assert p["to"] == ["bob@example.com"] and p["cc"] == ["c@example.com"] and "bcc" not in p
    assert p["from"] == "RT Crackers Sales <sales@rtcrackers.com>"
    assert "\n" not in p["subject"] and "\r" not in p["subject"]
    [row] = service.list_messages(ADMIN, "sales", "sent")
    assert row["id"] == mid and row["to_addrs"] == ["bob@example.com"]


def test_send_mail_quotes_special_characters_in_display_name(db, fake_resend, monkeypatch):
    service.ensure_mailboxes()
    monkeypatch.setattr(service, "_box_by_local", lambda l, u, sending=False: {
        "mailbox_id": 1, "label": 'Sales, "Team"', "address": "sales@rtcrackers.com"})
    monkeypatch.setattr(service, "_store_sent", lambda *a, **k: 1)
    service.send_mail(ADMIN, "sales", "bob@example.com", "", "", "s", "b")
    assert fake_resend.sent[0]["from"] == '"Sales, \\"Team\\"" <sales@rtcrackers.com>'


def test_reply_threads_headers(db, fake_resend):
    service.ensure_mailboxes()
    service.ingest_received(received())
    parent = service.list_messages(ADMIN, "sales", "inbox")[0]["id"]
    service.send_mail(ADMIN, "sales", "jane@example.com", "", "", "Re: Hello", "thanks", parent)
    assert fake_resend.sent[0]["headers"]["In-Reply-To"] == "<r1@x>"


@pytest.mark.parametrize("args,msg", [
    (("sales", "", "", "", "body"), "at least one recipient"),
    (("sales", "bad", "", "", "body"), "Invalid email"),
    (("sales", "a@b.com", "", "", "  "), "Write a subject"),
    (("nope", "a@b.com", "", "", "x"), "Unknown mailbox"),
])
def test_send_mail_validation(db, fake_resend, args, msg):
    service.ensure_mailboxes()
    local, to, cc, bcc, body = args
    subject = "" if body.strip() == "" else "s"
    with pytest.raises(ValueError, match=msg):
        service.send_mail(ADMIN, local, to, cc, bcc, subject, body)
    assert fake_resend.sent == []


def test_send_to_disabled_mailbox_is_refused(db, fake_resend):
    service.ensure_mailboxes()
    from tests.conftest import _psql
    _psql("update mailboxes set is_sending_enabled=false where local_part='sales'")
    with pytest.raises(ValueError, match="disabled"):
        service.send_mail(ADMIN, "sales", "a@b.com", "", "", "s", "b")


def test_store_failure_after_send_raises_clear_error(db, fake_resend, monkeypatch):
    service.ensure_mailboxes()
    monkeypatch.setattr(service, "_store_sent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(service.SentNotSaved):
        service.send_mail(ADMIN, "sales", "a@b.com", "", "", "s", "b")
    assert len(fake_resend.sent) == 1  # it really was sent


def test_sync_inbox_paginates_and_counts(db, monkeypatch, fake_resend):
    service.ensure_mailboxes()
    pages = {None: {"data": [{"id": "a", "to": ["sales@rtcrackers.com"]}], "has_more": True},
             "a": {"data": [{"id": "b", "to": ["info@rtcrackers.com"]}], "has_more": False}}
    monkeypatch.setattr(resend_client, "list_received", lambda limit=100, after=None: pages[after])
    assert service.sync_inbox(full_scan=True) == {"new": 2, "failed": 0, "partial": False}


def test_sync_is_single_flight(db, monkeypatch):
    service.ensure_mailboxes()
    assert service._sync_lock.acquire(blocking=False)
    try:
        assert service.sync_inbox()["busy"] is True
    finally:
        service._sync_lock.release()


def test_sync_one_bad_message_does_not_abort(db, monkeypatch):
    service.ensure_mailboxes()

    def get_received(i):
        if i == "bad":
            raise resend_client.ResendError(404, "gone")
        return {"from": "j@example.com", "subject": "ok", "text": "t"}

    monkeypatch.setattr(resend_client, "get_received", get_received)
    monkeypatch.setattr(resend_client, "list_received", lambda limit=100, after=None: {
        "data": [{"id": "bad", "to": ["sales@rtcrackers.com"]}, {"id": "good", "to": ["sales@rtcrackers.com"]}],
        "has_more": False})
    assert service.sync_inbox() == {"new": 1, "failed": 1, "partial": False}


# --- Drafts -------------------------------------------------------------------

def test_save_draft_creates_then_updates_in_place(db, fake_resend):
    service.ensure_mailboxes()
    d = service.save_draft(ADMIN, "sales", "bob@example.com, half-typed", "", "", "Quote", "Draft body")
    assert d["id"] and d["saved_at"]
    again = service.save_draft(ADMIN, "sales", "bob@example.com, ann@example.com", "", "", "Quote v2", "More text", d["id"])
    assert again["id"] == d["id"]

    [row] = service.list_messages(ADMIN, "sales", "drafts")
    assert row["subject"] == "Quote v2" and row["to_addrs"] == ["bob@example.com", "ann@example.com"]
    assert service.draft_counts(ADMIN) == {"sales": 1}
    assert service.list_messages(ADMIN, "sales", "inbox") == []   # drafts never leak into other folders
    assert service.list_messages(ADMIN, "sales", "sent") == []
    assert service.counts(ADMIN) == {}                            # and are never "unread"


def test_incomplete_addresses_are_skipped_not_rejected(db):
    service.ensure_mailboxes()
    d = service.save_draft(ADMIN, "sales", "bob@exa, ok@example.com", "", "", "", "hi")
    msg = service.get_message(ADMIN, d["id"])
    assert msg["to_addrs"] == ["ok@example.com"] and msg["folder"] == "drafts" and msg["text_body"] == "hi"


def test_empty_draft_is_not_created_but_existing_one_can_be_emptied(db):
    service.ensure_mailboxes()
    assert service.save_draft(ADMIN, "sales", "", "", "", "  ", "  \n")["id"] is None
    assert service.draft_counts(ADMIN) == {}
    d = service.save_draft(ADMIN, "sales", "", "", "", "x", "")
    assert service.save_draft(ADMIN, "sales", "", "", "", "", "", d["id"])["id"] == d["id"]


def test_draft_can_move_between_mailboxes(db):
    service.ensure_mailboxes()
    d = service.save_draft(ADMIN, "sales", "a@example.com", "", "", "s", "b")
    service.save_draft(ADMIN, "support", "a@example.com", "", "", "s", "b", d["id"])
    assert service.list_messages(ADMIN, "sales", "drafts") == []
    assert len(service.list_messages(ADMIN, "support", "drafts")) == 1


def test_delete_draft(db):
    service.ensure_mailboxes()
    d = service.save_draft(ADMIN, "sales", "a@example.com", "", "", "s", "b")
    assert service.delete_draft(ADMIN, d["id"]) is True
    assert service.delete_draft(ADMIN, d["id"]) is False
    assert service.list_messages(ADMIN, "sales", "drafts") == []


def test_draft_permissions(db):
    service.ensure_mailboxes()
    sales_id = next(b["mailbox_id"] for b in service.list_mailboxes(ADMIN) if b["local_part"] == "sales")
    scoped = User("u", "u@x", False, {sales_id})
    d = service.save_draft(ADMIN, "support", "a@example.com", "", "", "secret", "b")
    # cannot read, edit or delete a draft in a mailbox they cannot access
    assert service.get_message(scoped, d["id"]) is None
    with pytest.raises(LookupError):
        service.save_draft(scoped, "sales", "a@example.com", "", "", "hijack", "b", d["id"])
    assert service.delete_draft(scoped, d["id"]) is False
    with pytest.raises(LookupError):
        service.save_draft(scoped, "support", "a@example.com", "", "", "s", "b")
    assert service.draft_counts(scoped) == {}
    # but they can manage their own
    own = service.save_draft(scoped, "sales", "a@example.com", "", "", "mine", "b")
    assert service.draft_counts(scoped) == {"sales": 1} and service.delete_draft(scoped, own["id"])


def test_sent_messages_cannot_be_edited_or_deleted_as_drafts(db, fake_resend):
    service.ensure_mailboxes()
    mid = service.send_mail(ADMIN, "sales", "a@example.com", "", "", "s", "b")
    assert service.delete_draft(ADMIN, mid) is False
    with pytest.raises(LookupError):
        service.save_draft(ADMIN, "sales", "a@example.com", "", "", "tamper", "b", mid)


def test_sending_a_draft_removes_it_and_creates_sent_copy(db, fake_resend):
    service.ensure_mailboxes()
    d = service.save_draft(ADMIN, "sales", "a@example.com", "", "", "Hello", "body")
    mid = service.send_mail(ADMIN, "sales", "a@example.com", "", "", "Hello", "body final", draft_id=d["id"])
    assert service.draft_counts(ADMIN) == {}
    [row] = service.list_messages(ADMIN, "sales", "sent")
    assert row["id"] == mid and fake_resend.sent[0]["text"] == "body final"


def test_failed_send_keeps_the_draft(db, monkeypatch):
    service.ensure_mailboxes()
    monkeypatch.setattr(resend_client, "send",
                        lambda p: (_ for _ in ()).throw(resend_client.ResendError(502, "down")))
    d = service.save_draft(ADMIN, "sales", "a@example.com", "", "", "Hello", "body")
    with pytest.raises(resend_client.ResendError):
        service.send_mail(ADMIN, "sales", "a@example.com", "", "", "Hello", "body", draft_id=d["id"])
    assert service.draft_counts(ADMIN) == {"sales": 1}


def test_sent_but_not_saved_keeps_the_draft(db, fake_resend, monkeypatch):
    service.ensure_mailboxes()
    d = service.save_draft(ADMIN, "sales", "a@example.com", "", "", "Hello", "body")
    monkeypatch.setattr(service, "_store_sent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(service.SentNotSaved):
        service.send_mail(ADMIN, "sales", "a@example.com", "", "", "Hello", "body", draft_id=d["id"])
    assert service.draft_counts(ADMIN) == {"sales": 1}


def test_reply_draft_keeps_threading_through_send(db, fake_resend):
    service.ensure_mailboxes()
    service.ingest_received(received())
    parent = service.list_messages(ADMIN, "sales", "inbox")[0]["id"]
    d = service.save_draft(ADMIN, "sales", "jane@example.com", "", "", "Re: Hello", "wip", reply_to_id=parent)
    # resumed later: the client only knows the draft id, not the original message
    service.save_draft(ADMIN, "sales", "jane@example.com", "", "", "Re: Hello", "done", d["id"])
    service.send_mail(ADMIN, "sales", "jane@example.com", "", "", "Re: Hello", "done", draft_id=d["id"])
    assert fake_resend.sent[0]["headers"]["In-Reply-To"] == "<r1@x>"
    assert service.draft_counts(ADMIN) == {}


def test_draft_search(db):
    service.ensure_mailboxes()
    service.save_draft(ADMIN, "sales", "a@example.com", "", "", "Invoice", "pay soon")
    assert len(service.list_messages(ADMIN, "sales", "drafts", "invoice")) == 1
    assert service.list_messages(ADMIN, "sales", "drafts", "zzz") == []


# --- Gmail-style actions: stars, trash, read state, search operators, pagination, delivery --------

@pytest.fixture
def inbox3(db, fake_resend, monkeypatch):
    """Three received messages in sales from different senders."""
    service.ensure_mailboxes()
    people = {"r1": ("Jane <jane@example.com>", "Order question", "Where is my order?"),
              "r2": ("Bob <bob@example.com>", "Invoice 42", "Please pay"),
              "r3": ("Cara <cara@other.org>", "Hello", "Just saying hi")}

    def get_received(i):
        frm, subj, text = people[i]
        return {"from": frm, "subject": subj, "text": text, "created_at": "2025-03-0%d 05:06:07+00" % int(i[1])}

    monkeypatch.setattr(resend_client, "get_received", get_received)
    for rid in people:
        service.ingest_received(received(rid))
    rows = service.list_messages(ADMIN, "sales", "inbox")
    return {r["subject"]: r["id"] for r in rows}


def test_star_and_unstar_show_in_starred_view(inbox3):
    assert service.update_messages(ADMIN, [inbox3["Invoice 42"]], "star") == 1
    assert service.update_messages(ADMIN, [inbox3["Invoice 42"]], "star") == 0   # already starred: nothing changed
    [row] = service.list_messages(ADMIN, "sales", "starred")
    assert row["subject"] == "Invoice 42" and row["is_starred"] is True and row["folder"] == "inbox"
    assert service.get_message(ADMIN, row["id"])["is_starred"] is True
    assert service.update_messages(ADMIN, [row["id"]], "unstar") == 1
    assert service.list_messages(ADMIN, "sales", "starred") == []


def test_starred_view_includes_sent_mail_too(inbox3, fake_resend):
    mid = service.send_mail(ADMIN, "sales", "a@example.com", "", "", "Out", "b")
    service.update_messages(ADMIN, [mid], "star")
    assert [r["folder"] for r in service.list_messages(ADMIN, "sales", "starred")] == ["sent"]


def test_trash_restore_and_unread_counts(inbox3):
    assert service.counts(ADMIN) == {"sales": 3}
    jane = inbox3["Order question"]
    assert service.update_messages(ADMIN, [jane], "trash") == 1
    assert service.counts(ADMIN) == {"sales": 2}                       # trashed mail is not "unread"
    assert jane not in [r["id"] for r in service.list_messages(ADMIN, "sales", "inbox")]
    [t] = service.list_messages(ADMIN, "sales", "trash")
    assert t["id"] == jane and t["deleted"] is True
    # a trashed message can still be opened, and viewing it in Trash does not mark it read
    msg = service.get_message(ADMIN, jane)
    assert msg["deleted"] is True and msg["is_read"] is False
    assert service.update_messages(ADMIN, [jane], "restore") == 1
    assert service.counts(ADMIN) == {"sales": 3} and service.list_messages(ADMIN, "sales", "trash") == []


def test_delete_forever_only_works_from_trash(inbox3):
    jane = inbox3["Order question"]
    assert service.update_messages(ADMIN, [jane], "delete_forever") == 0   # not in trash: refused
    service.update_messages(ADMIN, [jane], "trash")
    assert service.update_messages(ADMIN, [jane], "delete_forever") == 1
    assert service.get_message(ADMIN, jane) is None


def test_empty_trash_is_scoped_to_one_mailbox(inbox3, fake_resend):
    service.update_messages(ADMIN, list(inbox3.values()), "trash")
    other = service.send_mail(ADMIN, "support", "a@example.com", "", "", "keep", "b")
    service.update_messages(ADMIN, [other], "trash")
    assert service.empty_trash(ADMIN, "sales") == 3
    assert len(service.list_messages(ADMIN, "support", "trash")) == 1


def test_mark_read_and_unread(inbox3):
    ids = list(inbox3.values())
    assert service.update_messages(ADMIN, ids, "read") == 3 and service.counts(ADMIN) == {}
    assert service.update_messages(ADMIN, ids[:2], "unread") == 2 and service.counts(ADMIN) == {"sales": 2}


def test_bulk_actions_respect_mailbox_permissions_and_skip_drafts(inbox3):
    sales_id = next(b["mailbox_id"] for b in service.list_mailboxes(ADMIN) if b["local_part"] == "support")
    scoped = User("u", "u@x", False, {sales_id})            # may only access support, not sales
    assert service.update_messages(scoped, list(inbox3.values()), "trash") == 0
    assert service.update_messages(scoped, list(inbox3.values()), "star") == 0
    draft = service.save_draft(ADMIN, "sales", "a@example.com", "", "", "wip", "x")
    assert service.update_messages(ADMIN, [draft["id"]], "trash") == 0   # drafts use delete_draft
    assert service.update_messages(ADMIN, [], "trash") == 0
    with pytest.raises(ValueError):
        service.update_messages(ADMIN, [1], "explode")
    with pytest.raises(ValueError):
        service.update_messages(ADMIN, list(range(service.MAX_PAGE + 1)), "trash")


def test_search_operators(inbox3):
    names = lambda q: sorted(r["subject"] for r in service.list_messages(ADMIN, "sales", "inbox", q))
    assert names("from:jane") == ["Order question"]
    assert names("from:other.org") == ["Hello"]
    assert names("subject:invoice") == ["Invoice 42"]
    assert names('subject:"Order question"') == ["Order question"]
    assert names("to:sales@rtcrackers.com") == ["Hello", "Invoice 42", "Order question"]
    assert names("from:bob pay") == ["Invoice 42"]                 # operator + free text combine
    assert names("from:bob hello") == []
    service.update_messages(ADMIN, [inbox3["Hello"]], "read")
    assert names("is:unread") == ["Invoice 42", "Order question"] and names("is:read") == ["Hello"]
    service.update_messages(ADMIN, [inbox3["Hello"]], "star")
    assert names("is:starred") == ["Hello"]
    assert names("from:%") == []                                    # wildcards in values stay literal


def test_pagination(inbox3):
    first = service.list_messages(ADMIN, "sales", "inbox", limit=2)
    rest = service.list_messages(ADMIN, "sales", "inbox", limit=2, offset=2)
    assert len(first) == 2 and len(rest) == 1
    assert [r["subject"] for r in first + rest] == ["Hello", "Invoice 42", "Order question"]   # newest first
    assert service.list_messages(ADMIN, "sales", "inbox", limit=2, offset=10) == []


def test_delivery_events_update_sent_status(inbox3, fake_resend):
    mid = service.send_mail(ADMIN, "sales", "a@example.com", "", "", "Out", "b")
    assert service.list_messages(ADMIN, "sales", "sent")[0]["status"] == "sent"
    assert service.apply_delivery_event("email.delivered", "out-1", "evt1") == 1
    assert service.get_message(ADMIN, mid)["status"] == "delivered"
    assert service.apply_delivery_event("email.bounced", "out-1", "evt2") == 1
    assert service.apply_delivery_event("email.delivered", "out-1", "evt3") == 0   # late event never hides a bounce
    assert service.get_message(ADMIN, mid)["status"] == "bounced"
    assert service.apply_delivery_event("email.delivered", "unknown-id") == 0
    assert service.apply_delivery_event("email.opened", "out-1") == 0
    assert service.apply_delivery_event("email.delivered", None) == 0


def test_mail_for_unknown_company_address_lands_in_catch_all(db, fake_resend):
    """Resend delivers every address at the domain; mail for an address with no mailbox must not be lost."""
    service.ensure_mailboxes()
    r = service.ingest_received(received(to="nobody@rtcrackers.com"))
    assert r["ours"] is True and r["new"] == 1 and r["catch_all"] is True
    [row] = service.list_messages(ADMIN, "admin", "inbox")
    assert row["from_address"] == "jane@example.com"
