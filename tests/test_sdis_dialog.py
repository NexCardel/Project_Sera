"""The Distill dialog (Part L): built offscreen from a hand-made state, no mining, no real client data."""

import json
from types import SimpleNamespace

import pytest

from core.sdis import config, distill, register, store
from core.sdis.relevance import Datapoint

PORTAL = "GST Portal"
LINK = "https://services.gst.gov.in/services/auth/fowelcome"


class FakeDb:
    """Just the synced-table calls the dialog makes."""

    def __init__(self, admin=True):
        self.admin = admin
        self.decisions = {}
        self.mcl = []
        self.containers = None
        self.version = 0
        self.renamed = []

    def is_admin_pc(self):
        return self.admin

    def list_sdis_decisions(self):
        return [{"signature": s, "decision": d, "label": l} for s, (d, l) in self.decisions.items()]

    def set_sdis_decision(self, signature, decision, label=""):
        self.decisions[signature] = (decision, label)
        return signature

    def list_sdis_mcl(self, active_only=False):
        return list(self.mcl)

    def add_sdis_mcl(self, name, label="", value_type="", cls="", portal="all", created_by=""):
        if not any(r["name"] == name for r in self.mcl):
            self.mcl.append({"gid": "g" + name, "name": name, "label": label, "value_type": value_type,
                             "class": cls, "portal": portal})
        return "g" + name

    def rename_sdis_mcl(self, gid, label):
        self.renamed.append((gid, label))
        for r in self.mcl:
            if r["gid"] == gid:
                r["label"] = label
        return True

    def get_sdis_containers(self):
        if self.containers is None:
            return None
        return {"doc": self.containers, "version": self.version, "updated_by": "t", "updated_at": ""}

    def put_sdis_containers(self, doc, updated_by="", portals=None):
        self.containers = json.loads(json.dumps(doc))
        self.version += 1
        return self.version


def _dp(label, rel=80, sure=90, cls="profile", pages=2, reason=""):
    return Datapoint(key=(label, "text"), label=label, value_type="text", relevance_pct=rel, sure_pct=sure,
                     pages=[(LINK + str(i), 1, "chrome", 0.8) for i in range(pages)], nodes=[],
                     slot_type_pct=90, surprise=False, suggested_class=cls, class_reason=reason)


def _state(*dps):
    st = store.new_state()
    st["datapoints"] = store.datapoint_dicts(list(dps))
    st["last_run"] = "2026-10-05T14:05:00"
    return st


@pytest.fixture(scope="module")
def app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _dialog(app, db=None, dps=None, **kw):
    from ui.dialogs.sdis_dialog import SdisDialog
    db = db or FakeDb()
    dps = dps if dps is not None else [_dp("Legal name"), _dp("Trade name", rel=60, cls="dataset"),
                                        _dp("Status", rel=40, sure=50, cls=None, reason="clients disagree")]
    return SdisDialog(db, state=_state(*dps), portals=[PORTAL, "Income Tax"], portal_of=lambda dp: PORTAL, **kw), db


@pytest.fixture(autouse=True)
def _fresh_config(tmp_path, monkeypatch):
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "sdis_containers.json"))
    config._current, config._errors = None, []
    yield
    config._current, config._errors = None, []


def test_dialog_builds_from_a_fake_state_with_three_datapoints(app):
    dlg, _ = _dialog(app)
    assert dlg.tree.topLevelItemCount() == 3
    assert dlg.card_n["found"].text() == "3"
    assert dlg.card_n["little"].text() == "0"
    assert "Last run" in dlg.last_run.text()
    assert dlg.portal_bar.count() == 2


def test_a_datapoint_row_has_its_page_children(app):
    dlg, _ = _dialog(app)
    top = dlg.tree.topLevelItem(0)
    assert top.childCount() == 2
    assert "% of clients" in top.child(0).text(0)
    assert top.text(5) == "2 pages"


def test_relevance_filter_and_search(app):
    dlg, _ = _dialog(app)
    dlg.rel_f.setCurrentIndex(dlg.rel_f.findData(70))
    assert dlg.tree.topLevelItemCount() == 1
    dlg.rel_f.setCurrentIndex(dlg.rel_f.findData(0))
    dlg.search.setText("trade")
    assert dlg.tree.topLevelItemCount() == 1


def test_editing_a_label_saves_it_at_once(app):
    dlg, db = _dialog(app)
    calls = []
    real = db.set_sdis_decision
    db.set_sdis_decision = lambda *a, **k: calls.append(a) or real(*a, **k)
    dlg.tree.topLevelItem(0).setText(0, "Business name")
    assert calls and calls[0][1] == distill.LABEL and calls[0][2] == "Business name"
    assert dlg.tree.topLevelItem(0).text(0) == "Business name"
    dlg.reload(_state(_dp("Legal name"), _dp("Trade name", cls="dataset"), _dp("Status", cls=None)))
    assert [dlg.tree.topLevelItem(i).text(0) for i in range(3)][0] == "Business name"   # a run never overwrites it


def test_editing_the_label_of_a_registered_datapoint_renames_its_field(app):
    dlg, db = _dialog(app)
    dp = dlg.dps[0]
    db.add_sdis_mcl("legal_name", "Legal name", "text", "profile", PORTAL)
    dlg.decided.registered[dp.key] = "legal_name"
    assert dlg.save_label(dp, "Registered name")
    assert db.renamed == [("glegal_name", "Registered name")]
    assert not dlg.save_label(dp, "  ")


def test_dismiss_is_saved_and_hides_the_row(app):
    dlg, db = _dialog(app)
    dlg.dismiss(dlg.dps[1])
    assert dlg.tree.topLevelItemCount() == 2
    assert dlg.state["rejected"] == [("Trade name", "text")]
    assert any(s.startswith(distill.DISMISS) for s in db.decisions)


def test_add_to_a_dataset_container_registers_the_field(app, monkeypatch):
    dlg, db = _dialog(app)
    db.containers = {"version": 1, "profile": {}, "containers": [
        {"name": "Returns", "portal": PORTAL, "form_field": None, "period_field": None, "fields": ["status"],
         "exceptions": {}}]}
    db.add_sdis_mcl("status", "Status", "text", "dataset", PORTAL)
    monkeypatch.setattr(config, "add_field", lambda doc, where, field, **kw: {**doc, "added": [where, field]})
    ok, msg = dlg.add_to(dlg.dps[1], "Returns")
    assert ok, msg
    assert db.containers["added"] == ["Returns", "trade_name"]
    assert dlg.decided.registered[dlg.dps[1].key] == "trade_name"
    assert dlg.state["picked"] == [("Trade name", "text")]


def test_a_refused_container_edit_gives_the_reason(app, monkeypatch):
    dlg, db = _dialog(app)

    def refuse(*a, **k):
        raise config.ConfigError("a container needs at least one field")
    monkeypatch.setattr(config, "add_field", refuse)
    ok, msg = dlg.add_to(dlg.dps[1], "Returns")
    assert not ok and "at least one field" in msg
    assert dlg.dps[1].key not in dlg.decided.registered


def test_register_asks_before_it_registers(app):
    dlg, db = _dialog(app)
    asked = []
    dlg._ask = lambda title, text: asked.append(text) or False
    item = dlg.tree.topLevelItem(0)
    dlg.tree.setCurrentItem(item)
    dlg.register_selected()
    assert asked == ["Capture Legal name on every PC?"]
    assert db.decisions == {}


def test_please_check_keep_and_template(app):
    dlg, db = _dialog(app)
    triple = (LINK, "Aaaa", "Active")
    dlg.items = [{"triple": triple, "text": "Active", "where": LINK, "saw": "x"}]
    dlg._fill_check()
    assert dlg.check_tree.topLevelItemCount() == 1
    dlg.answer(triple, distill.REJECT)
    assert triple in dlg.state["rejected_triples"]
    dlg.answer(triple, distill.KEEP)
    assert triple not in dlg.state["rejected_triples"]


def test_the_loading_dialog_is_application_modal_and_never_starts_by_itself(app):
    from PySide6.QtCore import Qt
    from ui.dialogs.sdis_dialog import SdisLoadingDialog

    class Client:
        started, cancelled = [], 0

        def __init__(self, on_progress=None, on_done=None):
            self.on_progress, self.on_done = on_progress, on_done

        def start(self, captures, state=None, rebuild=False):
            Client.started.append(captures)

        def cancel(self):
            Client.cancelled += 1
            self.on_done({"result": "cancelled"})

    dlg = SdisLoadingDialog(None, Client)
    assert dlg.windowModality() == Qt.ApplicationModal
    assert Client.started == []
    dlg.start("folder", "state.gz")
    assert Client.started == ["folder"]
    dlg.client.on_progress(3, 12, "https://example.test/page")
    app.processEvents()
    assert dlg.working.text() == "Working on 3 of 12 - https://example.test/page"
    dlg.cancel_btn.click()
    app.processEvents()
    assert Client.cancelled == 1 and dlg.result == {"result": "cancelled"}


def test_find_datapoints_reloads_on_done(app, monkeypatch, tmp_path):
    from ui.dialogs import sdis_dialog

    dlg, _ = _dialog(app)
    monkeypatch.setattr(distill, "stage_default", lambda: tmp_path)

    class Client:
        def __init__(self, on_progress=None, on_done=None):
            self.on_done = on_done

        def start(self, captures, state=None, rebuild=False):
            self.on_done({"result": "ok", "cancelled": False})

        def cancel(self):
            pass

    dlg._client_factory = Client
    dlg.find_datapoints()
    app.processEvents()
    assert dlg._loading_dlg is None
    assert dlg.note.text().startswith("Found 3")


def test_non_admin_pc_gets_the_message(monkeypatch):
    from ui.windows import tracker_dump_window as tdw

    shown = []
    monkeypatch.setattr(tdw, "QMessageBox", SimpleNamespace(
        information=lambda parent, title, text: shown.append((title, text))))
    fake = SimpleNamespace(db=FakeDb(admin=False))
    tdw.TrackerDumpWindow._open_distill(fake)
    assert shown == [("Sera Distill", "Sera Distill runs on the admin PC only.")]
    assert not hasattr(fake, "_distill_dialog")


def test_register_value_shapes_reads_real_list_nodes():
    from core.sdis.memory import PageMemory

    pm = PageMemory("page", 3, link=LINK)
    pm.nodes = [{"shape": "Aaaa", "text": "x", "clients": {"c1": {"texts": ["AB12"], "sure": True},
                                                          "c2": {"texts": ["CD34"], "sure": False}}}]
    dp = SimpleNamespace(nodes=[(0, 0)])
    assert register.value_shapes(dp, [pm]) == {register.shape_of("AB12"): 1}


def test_roll_seen_reports_keys_new_since_the_run_before(tmp_path):
    path = tmp_path / "seen.json"
    a, b = ("A", "text"), ("B", "text")
    assert distill.roll_seen(path, "run1", [a]) == set()
    assert distill.roll_seen(path, "run1", [a]) == set()
    assert distill.roll_seen(path, "run2", [a, b]) == {b}
    assert distill.roll_seen(path, "run2", [a, b]) == {b}


def test_stage_inputs_prefixes_each_device_and_refreshes(tmp_path):
    one, two, dest = tmp_path / "one", tmp_path / "two", tmp_path / "dest"
    one.mkdir()
    two.mkdir()
    (one / "sdis_2026-10-05.jsonl").write_text("a\n")
    (two / "sdis_2026-10-05.jsonl").write_text("bb\n")
    distill.stage_inputs(dest, [("local", one), ("d2", two)])
    assert sorted(p.name for p in dest.iterdir()) == ["sdis_d2__2026-10-05.jsonl", "sdis_local__2026-10-05.jsonl"]
    (one / "sdis_2026-10-05.jsonl").write_text("changed\n")
    distill.stage_inputs(dest, [("local", one), ("d2", two)])
    assert (dest / "sdis_local__2026-10-05.jsonl").read_text() == "changed\n"


def test_container_editor_shows_the_reason_of_a_refused_edit(app):
    import copy
    from ui.dialogs.sdis_containers_dialog import ContainerEditor

    known = {"fields": ["a", "b"], "portals": ["P"]}
    doc = config.add_container(copy.deepcopy(config.current()), "C", "P", ["a"], **known)
    ed = ContainerEditor(doc, "C", known, {"a": "A", "b": "B"}, lambda d: 1)
    assert not ed.apply(config.remove_field, "C", "a")
    assert "no field counts" in ed.status.text()
    assert ed.doc["containers"][0]["fields"] == ["a"]
    assert ed.apply(config.add_field, "C", "b")
    assert ed.doc["containers"][0]["fields"] == ["a", "b"]


def test_levels_table_value_round_trips(app):
    from ui.dialogs.sdis_containers_dialog import LevelsTable

    levels = [{"name": "Draft", "when": {"captured": 1}}]
    t = LevelsTable(levels, {"Draft": "Draft"})
    assert t.value() == (levels, {"Draft": "Draft"})
