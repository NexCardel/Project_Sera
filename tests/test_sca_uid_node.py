"""SCA UID node (docs/sca-uid-node-plan.md): cleanup rules, fingerprint + find, the picker server, the
watcher, and the per-service arm. Fictional data only: every page is built in this file."""

import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

from core.sca import uid_clean, uid_node
from core.sca.uid_picker import PickSession, candidates
from core.sca.uid_source_watch import UidSourceWatcher

EDIT, TEXT, GROUP = 50004, 50020, 50026
LINK = "https://portal.example/login#/step2"


def node(parent, ctype, name="", rect=(0, 0, 10, 10), id="", cls="", value="", pw=False):
    n = {"parent": parent, "depth": 0 if parent < 0 else 1, "ctype": ctype,
         "type_name": {EDIT: "Edit", TEXT: "Text", GROUP: "Group"}[ctype], "name": name,
         "rect": rect, "id": id, "cls": cls, "aria": "", "value": value}
    if pw:
        n["pw"] = True
    return n


def one_page(uid="27ABCDE1234F1Z5", user_id="user", banner=False, cls="form-control"):
    """A one-page login: label + username box (the UID is its value) + label + password box."""
    doc = [node(-1, GROUP, rect=(0, 0, 800, 600), cls="login")]
    if banner:
        doc.append(node(0, EDIT, "Search", (0, 0, 100, 20), cls=cls, value="zzz"))
    doc += [node(0, TEXT, "Username", (100, 100, 80, 20)),
            node(0, EDIT, "Username", (200, 100, 200, 24), id=user_id, cls=cls, value=uid),
            node(0, TEXT, "Password", (100, 140, 80, 20)),
            node(0, EDIT, "Password", (200, 140, 200, 24), id="pass", cls=cls, pw=True)]
    return [doc]


def two_page(uid_text="Logged in as abc@gmail.com", pw=True):
    """Page 2 of a two-page login: the UID as text, then the password box."""
    doc = [node(-1, GROUP, rect=(0, 0, 800, 600)),
           node(0, TEXT, uid_text, (100, 60, 300, 20)),
           node(0, TEXT, "Password", (100, 140, 80, 20))]
    if pw:
        doc.append(node(0, EDIT, "Password", (200, 140, 200, 24), pw=True))
    return [doc]


# ------------------------------------------------------------------ cleanup rules (WP5)

@pytest.mark.parametrize("text,rule,uid", [
    ("GSTIN: 27ABCDE1234F1Z5", "GSTIN", "27ABCDE1234F1Z5"),
    ("PAN ABCDE1234F", "PAN", "ABCDE1234F"),
    ("27ABCDE1234F1Z5", "PAN", ""),                  # a GSTIN's own PAN does not count
    ("Welcome abc@gmail.com", "EMAIL", "ABC@GMAIL.COM"),   # whole; the lookup also tries "ABC"
    ("User ID: abc12", "LOGIN_ID", "ABC12"),
    ("abc12", "LOGIN_ID", "ABC12"),
    ("hello there world", "LOGIN_ID", ""),
    ("", "WHOLE", ""),
])
def test_cleanup_rules_find_the_uid_in_the_text(text, rule, uid):
    assert uid_clean.clean(text, rule) == uid


def test_auto_rule_prefers_the_most_specific_rule():
    assert uid_clean.auto_rule("GSTIN: 27ABCDE1234F1Z5") == "GSTIN"
    assert uid_clean.auto_rule("abc@gmail.com") == "EMAIL"
    assert uid_clean.auto_rule("no uid here at all") == ""


# ------------------------------------------------------------------ fingerprint + find

def fp_of(docs, i, rule=""):
    return uid_node.fingerprint(docs, 0, i, rule)


def test_fingerprint_keeps_key_id_field_label_and_password_offset():
    fp = fp_of(one_page(), 2)
    assert fp["id"] == "user" and fp["field"] == "value" and fp["rule"] == "GSTIN"
    assert fp["anchor"] == "username" and (fp["pw_dx"], fp["pw_dy"]) == (0, -40)
    assert uid_node.loads(uid_node.dumps(fp)) == fp


def test_the_picked_node_is_found_again_with_a_new_uid():
    fp = fp_of(one_page(), 2)
    n, why = uid_node.find(one_page(uid="29PQRST5678K1Z2"), fp)
    assert why == "found" and uid_node.uid_of(n, fp) == "29PQRST5678K1Z2"


def test_an_inserted_box_of_the_same_kind_does_not_move_the_pick():
    fp = fp_of(one_page(), 2)
    n, why = uid_node.find(one_page(banner=True), fp)
    assert why == "found" and n["id"] == "user"


def test_a_renamed_id_and_class_is_still_found_by_its_label():
    fp = fp_of(one_page(), 2)
    n, why = uid_node.find(one_page(user_id="u2", cls="input-new"), fp)
    assert why == "found" and uid_node.uid_of(n, fp) == "27ABCDE1234F1Z5"


def test_two_page_login_reads_the_text_node_and_its_email_local_part():
    fp = fp_of(two_page(), 1, "EMAIL")
    assert fp["field"] == "name"
    n, why = uid_node.find(two_page("Logged in as xyz@gmail.com"), fp)
    assert why == "found" and uid_node.uid_of(n, fp) == "XYZ@GMAIL.COM"


def test_no_such_node_is_missing_and_never_a_guess():
    fp = fp_of(one_page(), 2)
    page = [[node(-1, GROUP, rect=(0, 0, 800, 600)), node(0, TEXT, "Something else", (5, 5, 50, 10))]]
    assert uid_node.find(page, fp) == (None, "missing")


def test_two_equal_candidates_are_ambiguous():
    fp = fp_of(two_page(), 1)
    fp.update({"key": "nope", "loose": "nope", "pw_dx": None})
    doc = two_page()[0]
    doc.insert(2, node(0, TEXT, "Logged in as other@gmail.com", (100, 80, 300, 20)))
    # both texts follow nothing but the group: same (empty) label -> stage 4 is skipped, nothing unique
    assert uid_node.find([doc], fp)[0] is None


def test_fast_path_needs_exactly_one_hit_of_the_same_type():
    fp = fp_of(one_page(), 2)
    one = {"hits": [{"ctype": EDIT, "name": "Username", "value": "27ABCDE1234F1Z5"}], "pw": 1}
    assert uid_node.fast_uid(one, fp) == ("27ABCDE1234F1Z5", 1)
    two = {"hits": one["hits"] * 2, "pw": 1}
    assert uid_node.fast_uid(two, fp) == (None, 1)
    assert uid_node.fast_uid(None, fp) == (None, 0)


# ------------------------------------------------------------------ picker server (WP2-3, D1)

def _post(url, body, host=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "text/plain", **({"Host": host} if host else {})})
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def test_candidates_skip_password_boxes_and_texts_without_a_uid():
    c = candidates(one_page())
    assert "0:2" in c and c["0:2"]["auto"] == "GSTIN"
    assert "0:4" not in c                    # the password box


def test_picker_serves_the_page_and_takes_one_answer_with_the_token():
    got = []
    s = PickSession(one_page(), LINK, "GST", got.append, ttl=30)
    s.start()
    try:
        html = urllib.request.urlopen(s.url, timeout=5).read().decode()
        assert '"pick"' in html and "27ABCDE1234F1Z5" in html and "Pw#" not in html
        base = s.url.rsplit("/", 1)[0]
        assert _post(base + "/wrong", {"token": s.token, "doc": 0, "i": 2, "rule": "GSTIN"}) == 404
        assert _post(s.url, {"token": "bad", "doc": 0, "i": 2, "rule": "GSTIN"}) == 403
        assert _post(s.url, {"token": s.token, "doc": 0, "i": 4, "rule": "WHOLE"}) == 403     # not a candidate
        assert _post(s.url, {"token": s.token, "doc": 0, "i": 2, "rule": "GSTIN"}) == 200
        assert len(got) == 1 and got[0]["id"] == "user" and got[0]["rule"] == "GSTIN"
    finally:
        s.stop()


def test_picker_refuses_a_foreign_host_header():
    s = PickSession(one_page(), LINK, "GST", lambda fp: None, ttl=30)
    s.start()
    try:
        assert _post(s.url, {"token": s.token, "doc": 0, "i": 2, "rule": "GSTIN"}, host="evil.example") == 404
    finally:
        s.stop()


def test_a_second_answer_is_refused():
    s = PickSession(one_page(), LINK, "GST", lambda fp: None, ttl=30)
    assert s.accept({"token": s.token, "doc": 0, "i": 2, "rule": "GSTIN"}) is not None
    assert s.accept({"token": s.token, "doc": 0, "i": 2, "rule": "GSTIN"}) is None


# ------------------------------------------------------------------ watcher (WP4)

class FakeReader:
    def __init__(self, docs, url=LINK):
        self.docs, self.url = docs, url
        self.url_reads = self.page_reads = self.fast_reads = 0

    def read_url(self, hwnd):
        self.url_reads += 1
        return self.url

    def read_docs(self, hwnd):
        self.page_reads += 1
        return self.docs

    def find_fast(self, hwnd, aid):
        self.fast_reads += 1
        hits = [{"ctype": n["ctype"], "name": n["name"], "value": n["value"]}
                for d in self.docs for n in d if n.get("id") == aid and not n.get("pw")]
        return {"hits": hits, "pw": sum(1 for d in self.docs for n in d if n.get("pw"))}


def watcher(reader, fp, active=True, fg=1, link=LINK):
    uids, warns = [], []
    src = [{"id": 7, "uid_page_link": link, "uid_node": uid_node.dumps(fp)}] if fp else []
    w = UidSourceWatcher(lambda: src, lambda: active, lambda s, u: uids.append((s, u)),
                         lambda s, r: warns.append((s, r)), reader=reader, foreground=lambda: fg)
    return w, uids, warns


def test_the_uid_reports_after_two_equal_reads_then_once_per_visit():
    r = FakeReader(one_page(uid="27ABCDE12"))          # still being typed
    w, uids, _ = watcher(r, fp_of(one_page(), 2))
    w.tick()
    r.docs = one_page()
    w.tick()
    assert uids == []                                  # changed: needs a second equal read
    w.tick()
    w.tick()
    assert uids == [(7, "27ABCDE1234F1Z5")]
    assert r.page_reads == 0 and r.fast_reads == 4      # the HTML id: fast path only


def test_leaving_and_coming_back_reports_again():
    r = FakeReader(one_page())
    w, uids, _ = watcher(r, fp_of(one_page(), 2))
    w.tick(); w.tick()
    r.url = "https://other.example/"
    assert w.tick() is False
    assert w.tick() is False                           # LEAVE_READS (2) reads away: left
    r.url = LINK
    w.tick(); w.tick()
    assert len(uids) == 2


def test_one_odd_address_bar_read_does_not_end_the_visit():
    """2026-11-09: one read off the page (3 ms later back on it) ended the visit, re-reported the UID
    and re-armed, so the SCA card slid out and in again."""
    r = FakeReader(one_page())
    w, uids, _ = watcher(r, fp_of(one_page(), 2))
    w.tick(); w.tick()
    assert len(uids) == 1
    r.url = ""                                         # the bar read came back empty once
    w.tick()
    r.url = LINK
    w.tick(); w.tick(); w.tick()
    assert len(uids) == 1 and w._visit is not None


def test_page_one_of_a_two_page_login_is_not_read_as_the_uid_page():
    fp = fp_of(two_page(), 1, "EMAIL")
    r = FakeReader(two_page(pw=False))
    w, uids, warns = watcher(r, fp)
    w.tick(); w.tick()
    assert uids == [] and warns == []
    r.docs = two_page()
    w.tick(); w.tick()
    assert uids == [(7, "ABC@GMAIL.COM")] and r.page_reads == 4    # no HTML id: full reads


def test_a_lost_node_warns_once_per_visit_and_never_arms():
    fp = fp_of(one_page(), 2)
    lost = [[node(-1, GROUP, rect=(0, 0, 800, 600)), node(0, EDIT, "Password", (1, 1, 5, 5), pw=True)]]
    r = FakeReader(lost)
    w, uids, warns = watcher(r, fp)
    w.tick(); w.tick(); w.tick()
    assert warns == []                                 # a page still being built is not "lost" yet
    for _ in range(5):
        w.tick()
    assert uids == [] and warns == [(7, "missing")]


def test_no_source_or_sca_off_or_no_browser_costs_no_uia():
    fp = fp_of(one_page(), 2)
    for kw in ({"fp": None}, {"active": False}, {"fg": 0}):
        r = FakeReader(one_page())
        w, uids, _ = watcher(r, kw.pop("fp", fp), **kw)
        assert w.tick() is False
        assert r.url_reads == r.page_reads == r.fast_reads == 0 and uids == []


# ------------------------------------------------------------------ per-service arm (WP6)

@pytest.fixture
def svc_db():
    from database import SeraDatabase
    d = tempfile.mkdtemp()
    db = SeraDatabase(os.path.join(d, "t.db"), "0123456789abcdef" * 4, raw_db_path=os.path.join(d, "r.db"))
    cols = db.get_mcl_columns()
    values = {c["id"]: f"Fictional Co {c['id']}" for c in cols if c.get("is_internal_pk")}
    pan = next(c["id"] for c in cols if c["label"].strip().upper() == "PAN")
    pwd = next(c["id"] for c in cols if "pass" in c["label"].lower())
    values.update({pan: "ABCPD1234E", pwd: "Pw#Fictional1"})
    a = db.create_service(name="Portal A", login_page_link="https://a.example/login", userid_column_id=pan,
                          password_column_id=pwd, username_selector="#u", password_selector="#p",
                          automation_mode="manual")
    b = db.create_service(name="Portal B", login_page_link="https://b.example/login", userid_column_id=pan,
                          password_column_id=pwd, username_selector="#u", password_selector="#p",
                          automation_mode="manual")
    c_ = db.create_service(name="Portal C", login_page_link="https://c.example/login", userid_column_id=pan,
                           password_column_id=pwd, username_selector="#u", password_selector="#p",
                           automation_mode="manual")
    client = db.add_client(values=values, notes="", service_ids=[a, b])
    yield db, client, a, b, c_
    shutil.rmtree(d, ignore_errors=True)


def _service(svc_db):
    from clipboard_watch import ClipboardWatchService
    db = svc_db[0]
    sent = []
    p = patch("automation.arm_sca", side_effect=lambda req, **k: sent.append(req))
    p.start()
    return ClipboardWatchService(db, parent=None), sent, p


def test_a_uid_page_arms_only_its_own_service(svc_db):
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    try:
        w.handle_source_uid(a, "abcpd1234e")
        assert len(sent) == 1
        arm = sent[-1]["arm"]
        assert arm["trigger"] == "source" and arm["client_id"] == client
        assert [s["service_id"] for s in arm["services"]] == [a]
        w.handle_source_uid(b, "ABCPD1234E")          # another of the client's services: armed for it only
        assert [s["service_id"] for s in sent[-1]["arm"]["services"]] == [b]
    finally:
        p.stop()


def test_a_uid_page_arms_nothing_for_a_service_the_client_lacks_or_an_unknown_uid(svc_db):
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    try:
        w.handle_source_uid(c_, "ABCPD1234E")
        w.handle_source_uid(a, "ZZZZZ9999Z")
        w.set_enabled(False)
        w.handle_source_uid(a, "ABCPD1234E")
        assert sent == []
    finally:
        p.stop()


def test_sources_need_both_a_link_and_a_picked_node(svc_db):
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    try:
        db.set_service_uid_page_links({a: LINK, b: LINK})
        db.set_service_uid_node(a, uid_node.dumps(fp_of(one_page(), 2)))
        w.refresh_sources()
        assert [s["id"] for s in w._uid_sources] == [a]
    finally:
        p.stop()


def test_a_refused_uid_page_says_why(svc_db):
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    notes = []
    w.sca_notice.connect(lambda t, lvl: notes.append((t, lvl)))
    try:
        w.handle_source_uid(a, "ZZZZZ9999Z")
        w.handle_source_uid(c_, "ABCPD1234E")
        assert sent == [] and len(notes) == 2 and all(lvl == "warning" for _, lvl in notes)
        assert "no client has it" in notes[0][0] and "ZZZZZ9999Z" in notes[0][0]
        assert "does not have" in notes[1][0]
        w.handle_source_uid(a, "ABCPD1234E")          # arms: no notice
        assert len(sent) == 1 and len(notes) == 2
    finally:
        p.stop()


# ------------------------------------------------------------------ box picked while empty (2026-11-07 fix)

def test_a_box_is_always_its_typed_value_never_its_label():
    fp = fp_of(one_page(uid=""), 2)                    # picked while the box was still empty
    assert fp["field"] == "value"
    n, why = uid_node.find(one_page(uid="ABCPD1234E"), fp)
    assert why == "found" and uid_node.uid_of(n, dict(fp, rule="PAN")) == "ABCPD1234E"


def test_an_old_pick_that_points_a_box_at_its_label_still_reads_the_value():
    fp = dict(fp_of(one_page(), 2), field="name", rule="LOGIN_ID")
    n, _ = uid_node.find(one_page(uid="ABCPD1234E"), fp)
    assert uid_node.uid_of(n, fp) == "ABCPD1234E"
    hit = {"hits": [{"ctype": EDIT, "name": "Username", "value": "ABCPD1234E"}], "pw": 1}
    assert uid_node.fast_uid(hit, fp) == ("ABCPD1234E", 1)


def test_an_empty_box_is_no_candidate_and_labels_are_weak():
    c = candidates(one_page(uid=""))
    assert "0:2" not in c                              # the empty box: not its label "Username"
    assert c and not any(x["strong"] for x in c.values())
    assert candidates(one_page(uid="abc12"))["0:2"]["strong"]
    assert candidates(two_page())["0:1"]["strong"]     # an email in a text


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class StepStop:
    """A stop event whose wait() advances the fake clock and can change the page between polls."""
    def __init__(self, clock, reader, pages):
        self.clock, self.reader, self.pages = clock, reader, list(pages)

    def is_set(self):
        return False

    def wait(self, s):
        self.clock.t += s
        if self.pages:
            self.reader.docs = self.pages.pop(0)


def _wait(pages, weak_wait=20, timeout=60):
    from core.sca.uid_picker import wait_for_page
    clock = Clock()
    r = FakeReader(pages[0])
    got = wait_for_page(LINK, r, lambda: 1, StepStop(clock, r, pages[1:]), timeout=timeout,
                        weak_wait=weak_wait, clock=clock)
    return got, clock.t


def test_the_picker_waits_until_the_uid_is_typed_and_complete():
    empty, half, full = one_page(uid=""), one_page(uid="ABCPD12"), one_page(uid="ABCPD1234E")
    got, t = _wait([empty, empty, half, full, full, full])
    assert got is not None and got[0] == full          # never the empty or half-typed read
    assert t < 20


def test_a_label_only_page_is_read_only_after_the_weak_wait():
    got, t = _wait([one_page(uid="")], weak_wait=5)
    assert got is not None and t >= 5


def test_the_watcher_says_so_when_the_box_shows_no_uid():
    fp = dict(fp_of(one_page(), 2), rule="PAN")
    r = FakeReader(one_page(uid=""))                   # empty box: normal, no warning
    w, uids, warns = watcher(r, fp)
    for _ in range(8):
        w.tick()
    assert warns == [] and uids == []
    r.docs = one_page(uid="ABCPD12")                   # a short typing pause: no warning either
    w.tick(); w.tick()
    assert warns == []
    r.docs = one_page(uid="not a pan here")            # stays: no UID in it
    for _ in range(12):
        w.tick()
    assert warns == [(7, "no_uid")] and uids == []


def test_no_uid_warning_text(svc_db):
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    notes = []
    w.sca_notice.connect(lambda t, lvl: notes.append((t, lvl)))
    try:
        db.set_service_uid_page_links({a: LINK})
        db.set_service_uid_node(a, uid_node.dumps(dict(fp_of(one_page(), 2), rule="PAN")))
        w.refresh_sources()
        w.handle_source_warn(a, "no_uid")
        assert len(notes) == 1 and "finds no UID" in notes[0][0] and "rule PAN" in notes[0][0]
        assert "Portal A" in notes[0][0]
    finally:
        p.stop()


# ------------------------------------------------------------------ unflagged password boxes (2026-11-07)

class _FakeUia:
    """Stands in for core.sgt_i.uia_nodes in page_nodes._node: the element IS its property dict."""
    PID_CONTROL_TYPE, PID_BOUNDING_RECT, PID_NAME, PID_AUTOMATION_ID = 1, 2, 3, 4
    PID_SELECTION_IS_SELECTED, PID_IS_PASSWORD, PID_VALUE = 5, 6, 7

    @staticmethod
    def _cached(el, pid):
        return el.get(pid)


@pytest.mark.parametrize("name,aid,cls", [
    ("Password*", "", ""), ("", "txtPwd", ""), ("", "", "form-control user_password"), ("Passcode", "", ""),
])
def test_a_box_named_like_a_password_is_a_password_box_and_its_value_is_never_read(name, aid, cls):
    from core.sca import page_nodes
    U = _FakeUia
    el = {U.PID_CONTROL_TYPE: EDIT, U.PID_NAME: name, U.PID_AUTOMATION_ID: aid,
          page_nodes.PID_CLASS_NAME: cls, U.PID_IS_PASSWORD: False, U.PID_VALUE: "Pw#Fictional1"}
    n = page_nodes._node(el, -1, 0, U)
    assert n["pw"] is True and n["value"] == ""


def test_a_username_box_still_reads_its_value():
    from core.sca import page_nodes
    U = _FakeUia
    el = {U.PID_CONTROL_TYPE: EDIT, U.PID_NAME: "User ID", U.PID_AUTOMATION_ID: "uid",
          U.PID_IS_PASSWORD: False, U.PID_VALUE: "ABCPD1234E"}
    n = page_nodes._node(el, -1, 0, U)
    assert "pw" not in n and n["value"] == "ABCPD1234E"
    assert not page_nodes.looks_like_password("Passport number", "Bypass", "")


def test_no_flagged_password_box_on_the_fast_path_falls_back_to_a_full_read():
    r = FakeReader(one_page())                         # the read flags the password box (by its label)
    r.find_fast = lambda hwnd, aid: {"hits": [{"ctype": EDIT, "name": "Username", "value": "27ABCDE1234F1Z5"}],
                                     "pw": 0}          # ...but UIA's IsPassword search finds none
    w, uids, _ = watcher(r, fp_of(one_page(), 2))
    w.tick(); w.tick()
    assert uids == [(7, "27ABCDE1234F1Z5")] and r.page_reads == 2


def test_a_uid_page_arm_carries_that_page_link(svc_db):
    """The extension needs it to act with nothing typed (Google's account chooser: one click)."""
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    try:
        db.set_service_uid_page_links({a: "https://accounts.portal.example/v3/signin/challenge/pwd?x=1"})
        db.set_service_uid_node(a, uid_node.dumps(fp_of(one_page(), 2)))
        w.refresh_sources()
        w.handle_source_uid(a, "ABCPD1234E")
        arm = sent[-1]["arm"]
        assert arm["trigger"] == "source" and arm["uid_page"] == "accounts.portal.example/v3/signin/challenge/pwd"
    finally:
        p.stop()


def test_a_copy_arm_has_no_uid_page(svc_db):
    import sca_protocol
    req = sca_protocol.build_arm_request(client_id=1, client_token="1", matched_uid="ABCPD1234E",
                                         candidate_uids=["ABCPD1234E"], services=[])
    assert req["arm"]["uid_page"] == "" and req["arm"]["trigger"] == "clipboard"


# ------------------------------------------------------------------ timing (2026-11-07)

def test_a_text_uid_reports_on_the_first_read_a_typed_box_needs_two():
    w, uids, _ = watcher(FakeReader(two_page()), fp_of(two_page(), 1, "EMAIL"))
    w.tick()
    assert uids == [(7, "ABC@GMAIL.COM")]
    w2, uids2, _ = watcher(FakeReader(one_page()), fp_of(one_page(), 2))
    w2.tick()
    assert uids2 == []
    w2.tick()
    assert uids2 == [(7, "27ABCDE1234F1Z5")]


def test_a_node_missing_on_one_read_of_a_loading_page_does_not_warn():
    fp = fp_of(two_page(), 1, "EMAIL")
    loading = [[node(-1, GROUP, rect=(0, 0, 800, 600)), node(0, EDIT, "Password", (1, 1, 5, 5), pw=True)]]
    r = FakeReader(loading)
    w, uids, warns = watcher(r, fp)
    w.tick()
    r.docs = two_page()
    w.tick()
    assert warns == [] and uids == [(7, "ABC@GMAIL.COM")]


def test_the_wait_is_short_while_a_browser_is_in_front_and_long_otherwise():
    from core.sca import uid_source_watch as m
    w, _, _ = watcher(FakeReader(one_page(), url="https://other.example/"), fp_of(one_page(), 2))
    assert w.tick() is False and w.browser_in_front and w.next_wait(False) == m.BROWSER_S
    w2, _, _ = watcher(FakeReader(one_page()), fp_of(one_page(), 2), fg=0)
    assert w2.tick() is False and not w2.browser_in_front and w2.next_wait(False) == m.IDLE_S
    assert w.next_wait(True) == m.TICK_S and m.BROWSER_S < 0.5 and m.TICK_S < 0.5


def test_a_wake_ends_the_wait_at_once():
    import time
    from core.sca import uid_source_watch as m
    calls = []
    r = FakeReader(one_page(), url="https://other.example/")
    w = m.UidSourceWatcher(lambda: [], lambda: True, lambda s, u: None, reader=r, foreground=lambda: 0)
    orig = w.tick
    w.tick = lambda: (calls.append(time.monotonic()), orig())[1]
    w.start(hook=False)                                # no sources: IDLE_S (3 s) between looks
    try:
        time.sleep(0.2)
        w.wake()
        time.sleep(0.3)
        assert len(calls) >= 2 and calls[1] - calls[0] < 1.0
    finally:
        w.stop()


def test_every_visit_of_the_uid_page_gets_a_fresh_arm(svc_db):
    """The watcher reports once per visit; the extension drops a used arm and never shows one arm twice in a
    tab. Keeping the live arm meant no widget on the next visit for 5 minutes."""
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    try:
        w.handle_source_uid(a, "ABCPD1234E")
        first = sent[-1]["arm"]["arm_id"]
        w.handle_fill_result({"arm_id": first, "service_id": a, "result": "filled"})     # used up
        w.handle_source_uid(a, "ABCPD1234E")                                             # next visit
        second = sent[-1]["arm"]["arm_id"]
        w.handle_source_uid(a, "ABCPD1234E")                                             # widget closed, back again
        assert len(sent) == 3 and len({first, second, sent[-1]["arm"]["arm_id"]}) == 3
    finally:
        p.stop()


def test_staying_on_the_uid_page_arms_once():
    r = FakeReader(two_page())
    w, uids, _ = watcher(r, fp_of(two_page(), 1, "EMAIL"))
    for _ in range(10):
        w.tick()
    assert uids == [(7, "ABC@GMAIL.COM")]


def test_uid_page_visits_without_a_fill_still_pause_the_client(svc_db):
    from clipboard_watch import UNUSED_ARMS_BEFORE_PAUSE
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    notes = []
    w.sca_notice.connect(lambda t, lvl: notes.append(t))
    try:
        for _ in range(UNUSED_ARMS_BEFORE_PAUSE + 2):
            w.handle_source_uid(a, "ABCPD1234E")
        assert len(sent) == UNUSED_ARMS_BEFORE_PAUSE + 1 and any("paused" in t for t in notes)
    finally:
        p.stop()


# ------------------------------------------------------------------ logs/sca.log (no UIDs, ever)

def test_sca_log_records_each_step_and_never_a_uid(svc_db, tmp_path, monkeypatch):
    log = tmp_path / "sca.log"
    monkeypatch.setenv("SERA_SCA_LOG", str(log))
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    try:
        r = FakeReader(two_page("Logged in as abcpd1234e@gmail.com"))
        watch, uids, _ = watcher(r, fp_of(two_page(), 1, "EMAIL"))
        watch.tick(); watch.tick()
        r.url = "https://other.example/"
        watch.tick(); watch.tick()                                # LEAVE_READS reads away: left
        w.handle_source_uid(a, uids[0][1])
        w.handle_source_uid(a, "ZZZZZ9999Z")
        text = log.read_text(encoding="utf-8")
        for step in ("UID page in front", "node found, UID of 20 characters", "report UID of 20 characters",
                     "left UID page", f"armed client #{client}", "no client has this UID"):
            assert step in text, step
        assert text.count("node found") == 1                     # logged on change only, not every read
        low = text.lower()
        assert "abcpd1234e" not in low and "zzzzz9999z" not in low and "gmail" not in low
    finally:
        p.stop()


# ------------------------------------------------------------------ full email + on-top notices (2026-11-07)

def test_an_email_uid_matches_a_client_by_the_whole_address_or_the_part_before_at(svc_db):
    from core.sca import uid_clean
    assert uid_clean.lookup_keys("ABC@GMAIL.COM") == ["ABC@GMAIL.COM", "ABC"]
    assert uid_clean.lookup_keys("ABCPD1234E") == ["ABCPD1234E"]
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    try:
        w._ensure_index_fresh()
        w._ensure_index_fresh = lambda: None
        w._uid_index["FICTIONAL.CO@GMAIL.COM"] = client       # a record that holds the whole address
        w.handle_source_uid(a, "fictional.co@gmail.com")
        assert sent[-1]["arm"]["matched_uid"] == "FICTIONAL.CO@GMAIL.COM"
        w._uid_index.pop("FICTIONAL.CO@GMAIL.COM")
        w._uid_index["SHORTCO"] = client                        # a record that holds the part before "@"
        w.handle_source_uid(a, "shortco@gmail.com")
        assert sent[-1]["arm"]["matched_uid"] == "SHORTCO" and len(sent) == 2
    finally:
        p.stop()


def test_uid_page_notices_also_go_on_top_of_the_browser(svc_db):
    db, client, a, b, c_ = svc_db
    w, sent, p = _service(svc_db)
    top = []
    w.source_notice.connect(lambda t, lvl: top.append(t))
    try:
        w.handle_source_uid(a, "nobody@gmail.com")
        w.handle_source_warn(a, "missing")
        assert len(top) == 2 and "also tried the part before @" in top[0] and "changed" in top[1]
    finally:
        p.stop()


def test_the_sca_log_of_a_test_run_is_never_the_real_one():
    from core.sca import sca_log
    from pathlib import Path
    assert sca_log.log_path() != Path.home() / "AmanAssociates_Sera" / "logs" / "sca.log"


def test_the_popup_shows_on_top_without_taking_focus(qapp=None):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])
    from ui.components.sca_notice_popup import ScaNoticePopup
    pop = ScaNoticePopup()
    pop.show_notice("SCA: test notice", "warning")
    try:
        f = pop.windowFlags()
        assert f & Qt.WindowStaysOnTopHint and f & Qt.WindowDoesNotAcceptFocus
        assert pop.testAttribute(Qt.WA_ShowWithoutActivating) and pop.isVisible()
    finally:
        pop.hide()
