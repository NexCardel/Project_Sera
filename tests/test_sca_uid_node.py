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
    ("Welcome abc@gmail.com", "EMAIL", "ABC"),       # the database stores the part before "@"
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
    assert why == "found" and uid_node.uid_of(n, fp) == "XYZ"


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
    r.url = LINK
    w.tick(); w.tick()
    assert len(uids) == 2


def test_page_one_of_a_two_page_login_is_not_read_as_the_uid_page():
    fp = fp_of(two_page(), 1, "EMAIL")
    r = FakeReader(two_page(pw=False))
    w, uids, warns = watcher(r, fp)
    w.tick(); w.tick()
    assert uids == [] and warns == []
    r.docs = two_page()
    w.tick(); w.tick()
    assert uids == [(7, "ABC")] and r.page_reads == 4    # no HTML id: full reads


def test_a_lost_node_warns_once_per_visit_and_never_arms():
    fp = fp_of(one_page(), 2)
    lost = [[node(-1, GROUP, rect=(0, 0, 800, 600)), node(0, EDIT, "Password", (1, 1, 5, 5), pw=True)]]
    r = FakeReader(lost)
    w, uids, warns = watcher(r, fp)
    w.tick(); w.tick(); w.tick()
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
        w.handle_source_uid(a, "ABCPD1234E")          # live arm for this client + service: no re-arm
        assert len(sent) == 1
        w.handle_source_uid(b, "ABCPD1234E")          # another of the client's services: re-armed for it
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
