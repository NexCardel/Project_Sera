"""core/sgt/sgt_repair.py: the plan from a PC's SGT log, and putting false rows back (docs/sgt-gst-fixes-plan.md WP7)."""
import json
import sqlite3
from contextlib import contextmanager

from core.dataset_key import compute_dataset_key
from core.sgt import sgt_repair as R

GSTIN = "29ABACD1191FAZK"
JUNE = {"form": "GSTR-3B", "period": "June (FY 2026-27)", "status": "Submitted & Verified",
        "status_evidence": "Filed", "fy": "2026-27", "tax_period": "June"}
OCT = dict(JUNE, period="October (FY 2026-27)", tax_period="October")
SEPT = dict(JUNE, period="September (FY 2026-27)", tax_period="September")


def ev(kind, **kw):
    return {"ts": "2026-10-06T10:23:46", "event": kind, "session": "s1", "portal": "GST Portal", **kw}


def write_log(tmp_path, events):
    d = tmp_path / "log"
    d.mkdir(exist_ok=True)
    (d / "sgt_shadow_2026-10-06.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return d


def moved(prev, new):
    return ev("dataset", change="moved", record="current_dataset", previous=prev, values=new)


PROFILE = [ev("profile", field="gstin", value=GSTIN), ev("profile", field="name", value="TEST CLIENT")]


class TestPlan:
    def test_a_moved_row_becomes_a_fix_with_its_old_values(self, tmp_path):
        plan = R.build_plan(write_log(tmp_path, PROFILE + [moved(JUNE, OCT)]), pc="PC1")
        assert len(plan["items"]) == 1
        it = plan["items"][0]
        assert it["gstin"] == GSTIN and it["restore"]["period"] == "June (FY 2026-27)"
        assert it["false"]["period"] == "October (FY 2026-27)"
        assert compute_dataset_key("GST Portal", GSTIN, "GSTR-3B", "June (FY 2026-27)") in it["restore_keys"]
        assert compute_dataset_key("GST Portal", GSTIN, "GSTR-3B", "October (FY 2026-27)") in it["false_keys"]

    def test_a_row_moved_twice_is_undone_back_to_its_start(self, tmp_path):
        plan = R.build_plan(write_log(tmp_path, PROFILE + [moved(JUNE, SEPT), moved(SEPT, OCT)]))
        assert len(plan["items"]) == 1
        assert plan["items"][0]["restore"]["period"] == "June (FY 2026-27)"
        assert plan["items"][0]["false"]["period"] == "October (FY 2026-27)"

    def test_the_same_row_moved_on_two_days_is_one_item(self, tmp_path):
        plan = R.build_plan(write_log(tmp_path, PROFILE + [moved(JUNE, OCT), moved(JUNE, OCT)]))
        assert len(plan["items"]) == 1

    def test_no_client_no_item_and_itr_is_ignored(self, tmp_path):
        itr = dict(moved(JUNE, OCT), portal="Income Tax")
        assert R.build_plan(write_log(tmp_path, [moved(JUNE, OCT), itr]))["items"] == []

    def test_a_same_period_move_is_not_a_fault(self, tmp_path):
        assert R.build_plan(write_log(tmp_path, PROFILE + [moved(JUNE, dict(JUNE, form="GSTR-1"))]))["items"] == []

    def test_filed_rows_without_an_arn_are_listed_for_review(self, tmp_path):
        row = ev("dataset", change="new", record="current_dataset", values=dict(JUNE))
        plan = R.build_plan(write_log(tmp_path, PROFILE + [row, row]))
        assert len(plan["review"]) == 1 and plan["review"][0]["gstin"] == GSTIN

    def test_write_plan_writes_json_and_csv(self, tmp_path):
        plan = R.build_plan(write_log(tmp_path, PROFILE + [moved(JUNE, OCT)]), pc="PC 1")
        paths = R.write_plan(plan, tmp_path / "out")
        assert {p.suffix for p in paths} == {".json", ".csv"} and all(p.exists() for p in paths)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tracker_dump (id INTEGER PRIMARY KEY, dataset_key TEXT, period_label TEXT, status TEXT, "
                 "arn_number TEXT, capture_method TEXT, raw_payload_json TEXT)")
    return conn


def put(conn, key, period, status="Submitted & Verified", arn="N/A", method="SGT_live"):
    payload = {"period_label": period, "status": status, "dataset_key": key, "client_name": "TEST CLIENT",
               "raw_payload": {"dataset_key": key, "sgt_dataset": dict(OCT, period=period, status=status)}}
    conn.execute("INSERT INTO tracker_dump (dataset_key, period_label, status, arn_number, capture_method, "
                 "raw_payload_json) VALUES (?,?,?,?,?,?)", (key, period, status, arn, method, json.dumps(payload)))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def a_plan(tmp_path):
    return R.build_plan(write_log(tmp_path, PROFILE + [moved(JUNE, OCT)]), pc="PC1")


FALSE_KEY = compute_dataset_key("GST Portal", GSTIN, "GSTR-3B", "October (FY 2026-27)")
TRUE_KEY = compute_dataset_key("GST Portal", GSTIN, "GSTR-3B", "June (FY 2026-27)")


class TestApply:
    def test_dry_run_changes_nothing(self, tmp_path):
        conn = make_db()
        rid = put(conn, FALSE_KEY, "October (FY 2026-27)")
        rep = R.apply_to_connection(conn, a_plan(tmp_path))
        assert rep[0]["result"] == "would put the row back"
        assert conn.execute("SELECT dataset_key FROM tracker_dump WHERE id=?", (rid,)).fetchone()[0] == FALSE_KEY

    def test_apply_puts_the_row_back_in_place_and_backs_up(self, tmp_path):
        conn = make_db()
        rid = put(conn, FALSE_KEY, "October (FY 2026-27)")
        bak = tmp_path / "bak" / "b.json"
        rep = R.apply_to_connection(conn, a_plan(tmp_path), apply=True, backup_to=bak)
        assert rep[0]["result"] == "put the row back"
        key, period, status, raw = conn.execute(
            "SELECT dataset_key, period_label, status, raw_payload_json FROM tracker_dump WHERE id=?", (rid,)).fetchone()
        assert (key, period, status) == (TRUE_KEY, "June (FY 2026-27)", "Submitted & Verified")
        p = json.loads(raw)
        assert p["period_label"] == "June (FY 2026-27)" and p["raw_payload"]["dataset_key"] == TRUE_KEY
        assert p["client_name"] == "TEST CLIENT" and "repaired" in p
        assert conn.execute("SELECT count(*) FROM tracker_dump").fetchone()[0] == 1
        assert json.loads(bak.read_text(encoding="utf-8"))["rows"][0]["before"][1] == FALSE_KEY

    def test_a_second_apply_does_nothing(self, tmp_path):
        conn = make_db()
        put(conn, FALSE_KEY, "October (FY 2026-27)")
        plan = a_plan(tmp_path)
        R.apply_to_connection(conn, plan, apply=True)
        again = R.apply_to_connection(conn, plan, apply=True)
        assert again[0]["result"].startswith("nothing to do")

    def test_if_the_right_row_exists_the_false_one_is_deleted(self, tmp_path):
        conn = make_db()
        put(conn, FALSE_KEY, "October (FY 2026-27)")
        put(conn, TRUE_KEY, "June (FY 2026-27)")
        R.apply_to_connection(conn, a_plan(tmp_path), apply=True)
        assert [r[0] for r in conn.execute("SELECT dataset_key FROM tracker_dump")] == [TRUE_KEY]

    def test_a_row_with_an_arn_or_another_status_is_left_alone(self, tmp_path):
        for arn, status in (("AA290121000069S", "Submitted & Verified"), ("N/A", "Draft")):
            conn = make_db()
            put(conn, FALSE_KEY, "October (FY 2026-27)", status=status, arn=arn)
            rep = R.apply_to_connection(conn, a_plan(tmp_path), apply=True)
            assert rep[0]["result"].startswith("skipped")
            assert conn.execute("SELECT dataset_key FROM tracker_dump").fetchone()[0] == FALSE_KEY

    def test_other_engines_rows_are_never_touched(self, tmp_path):
        conn = make_db()
        put(conn, FALSE_KEY, "October (FY 2026-27)", method="VSDC")
        rep = R.apply_to_connection(conn, a_plan(tmp_path), apply=True)
        assert rep[0]["result"].startswith("nothing to do")
        assert conn.execute("SELECT dataset_key FROM tracker_dump").fetchone()[0] == FALSE_KEY


class FakeDb:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def _connect_raw(self):
        yield self.conn
        self.conn.commit()


class TestStartupHook:
    def _setup(self, tmp_path):
        base = tmp_path / "sgt_repair"
        (base / "plans").mkdir(parents=True)
        (base / "plans" / "plan.json").write_text(json.dumps(a_plan(tmp_path)), encoding="utf-8")
        conn = make_db()
        rid = put(conn, FALSE_KEY, "October (FY 2026-27)")
        return base, conn, rid

    def test_without_the_apply_file_it_only_reports(self, tmp_path):
        base, conn, rid = self._setup(tmp_path)
        assert R.run_pending(FakeDb(conn), base, echo=lambda m: None) == 1
        assert conn.execute("SELECT dataset_key FROM tracker_dump WHERE id=?", (rid,)).fetchone()[0] == FALSE_KEY
        assert list((base / "reports").glob("*dryrun.json")) and (base / "plans" / "plan.json").exists()

    def test_with_the_apply_file_it_repairs_backs_up_and_retires_the_plan(self, tmp_path):
        base, conn, rid = self._setup(tmp_path)
        (base / "APPLY").write_text("", encoding="utf-8")
        assert R.run_pending(FakeDb(conn), base, echo=lambda m: None) == 1
        assert conn.execute("SELECT dataset_key FROM tracker_dump WHERE id=?", (rid,)).fetchone()[0] == TRUE_KEY
        assert list((base / "backups").glob("*.json")) and (base / "done" / "plan.json").exists()
        assert not (base / "plans" / "plan.json").exists()
        assert R.run_pending(FakeDb(conn), base, echo=lambda m: None) == 0

    def test_no_plans_no_work(self, tmp_path):
        assert R.run_pending(FakeDb(make_db()), tmp_path / "nothing", echo=lambda m: None) == 0
