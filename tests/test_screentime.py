from datetime import datetime

from core import screentime as st


def _reset():
    st._current.update(app_dir=None, started=None)


def test_start_and_clean_close_write_two_lines_with_the_identity_name(tmp_path):
    _reset()
    (tmp_path / "device_identity.txt").write_text("FRONT-DESK-1\nignored\n", encoding="utf-8")
    st.start(tmp_path, datetime(2026, 10, 12, 9, 0, 0))
    st.stop(datetime(2026, 10, 12, 13, 30, 0))
    lines = (tmp_path / "screentime.txt").read_text(encoding="utf-8").splitlines()
    assert lines == ["2026-10-12 09:00:00 | FRONT-DESK-1 | STARTED", "2026-10-12 13:30:00 | FRONT-DESK-1 | CLOSED"]
    assert not (tmp_path / "screentime_alive.txt").exists()
    rows = st.read_sessions(tmp_path)
    assert len(rows) == 1 and rows[0]["clean"] and rows[0]["seconds"] == 4 * 3600 + 1800
    assert st.duration_text(rows[0]["seconds"]) == "4 h 30 min"


def test_a_killed_run_is_closed_at_its_last_heartbeat_by_the_next_start(tmp_path):
    _reset()
    (tmp_path / "device_identity.txt").write_text("PC-B", encoding="utf-8")
    st.start(tmp_path, datetime(2026, 10, 12, 9, 0, 0))
    st.heartbeat(datetime(2026, 10, 12, 10, 15, 0))
    _reset()                                                  # the process died: no stop()
    st.start(tmp_path, datetime(2026, 10, 12, 11, 0, 0))
    rows = st.read_sessions(tmp_path, running_started=st.current_start())
    assert [r["running"] for r in rows] == [True, False]
    dead = rows[1]
    assert dead["closed"] == "2026-10-12 10:15:00" and not dead["clean"] and dead["seconds"] == 4500
    assert "not a clean close" in (tmp_path / "screentime.txt").read_text(encoding="utf-8")


def test_name_falls_back_to_the_hostname_and_pipes_are_removed(tmp_path):
    assert st.pc_name(tmp_path)
    (tmp_path / "device_identity.txt").write_text("A|B", encoding="utf-8")
    assert st.pc_name(tmp_path) == "A/B"


def test_missing_file_gives_no_rows_and_stop_without_start_is_silent(tmp_path):
    _reset()
    assert st.read_sessions(tmp_path) == []
    st.stop()
    assert not (tmp_path / "screentime.txt").exists()


def test_two_pcs_in_one_file_are_paired_separately(tmp_path):
    (tmp_path / "screentime.txt").write_text(
        "2026-10-12 09:00:00 | A | STARTED\n2026-10-12 09:05:00 | B | STARTED\n"
        "2026-10-12 10:00:00 | A | CLOSED\n2026-10-12 11:05:00 | B | CLOSED\n", encoding="utf-8")
    rows = {r["pc"]: r for r in st.read_sessions(tmp_path)}
    assert rows["A"]["seconds"] == 3600 and rows["B"]["seconds"] == 7200


class FakeDb:
    def __init__(self):
        self.s = {}

    def set_setting(self, k, v):
        self.s[k] = v

    def get_all_settings(self):
        return dict(self.s)


def test_each_pc_publishes_only_its_own_key_and_the_office_view_lists_every_pc(tmp_path):
    db = FakeDb()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "device_identity.txt").write_text("PC-A", encoding="utf-8")
    (b / "device_identity.txt").write_text("PC-B", encoding="utf-8")
    _reset(); st.attach(db, "dev-a")
    st.start(a, datetime(2026, 10, 12, 9, 0, 0)); st.stop(datetime(2026, 10, 12, 10, 0, 0))
    _reset(); st.attach(db, "dev-b")
    st.start(b, datetime(2026, 10, 12, 9, 30, 0))
    assert sorted(db.s) == ["screentime:dev-a", "screentime:dev-b"]
    rows = st.read_office(db, now=datetime(2026, 10, 12, 9, 40, 0))
    by_pc = {r["pc"]: r for r in rows}
    assert by_pc["PC-A"]["seconds"] == 3600 and by_pc["PC-A"]["clean"] and not by_pc["PC-A"]["running"]
    assert by_pc["PC-B"]["running"] and by_pc["PC-B"]["seconds"] == 600 and not by_pc["PC-B"]["stale"]
    # the same PC, seen a day later with no close: stale, not running
    late = {r["pc"]: r for r in st.read_office(db, now=datetime(2026, 10, 13, 9, 40, 0))}
    assert late["PC-B"]["stale"] and not late["PC-B"]["running"]
    _reset(); st.attach(None, None)


def test_heartbeat_republishes_only_every_publish_interval(tmp_path, monkeypatch):
    db = FakeDb()
    (tmp_path / "device_identity.txt").write_text("PC-A", encoding="utf-8")
    clock = [1000.0]
    monkeypatch.setattr(st.time, "monotonic", lambda: clock[0])
    _reset(); st.attach(db, "dev-a")
    st.start(tmp_path, datetime(2026, 10, 12, 9, 0, 0))
    first = db.s["screentime:dev-a"]
    clock[0] += 60
    st.heartbeat(datetime(2026, 10, 12, 9, 1, 0))
    assert db.s["screentime:dev-a"] == first
    clock[0] += st.PUBLISH_EVERY_S
    st.heartbeat(datetime(2026, 10, 12, 9, 11, 0))
    assert db.s["screentime:dev-a"] != first
    _reset(); st.attach(None, None)


def test_a_broken_database_never_stops_the_log(tmp_path):
    class Boom(FakeDb):
        def set_setting(self, k, v):
            raise RuntimeError("locked")
    _reset(); st.attach(Boom(), "x")
    st.start(tmp_path, datetime(2026, 10, 12, 9, 0, 0)); st.stop(datetime(2026, 10, 12, 9, 5, 0))
    assert len((tmp_path / "screentime.txt").read_text(encoding="utf-8").splitlines()) == 2
    _reset(); st.attach(None, None)
