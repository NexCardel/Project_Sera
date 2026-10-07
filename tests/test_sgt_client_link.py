"""
A portal user ID (an SDIS profile datapoint) names the saved client when the page itself does not show the
client's GSTIN. All identifiers are fictional.
"""
import json
from datetime import date

from PIL import Image

from core.sgt.sgt_client_link import ClientLink
from core.sgt.sgt_shadow import CLIENT_LINK_SPEC, SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore

GST = "GST Portal"
URL = "https://services.gst.gov.in/services/auth/fowelcome"
USERNAME_SPEC = {
    "name": "sdis.username", "field": "username", "portals": [GST], "labels": ["Username"], "within": 2,
    "pattern": "(?<![A-Za-z0-9])([A-Za-z]{4}_\\d{4})(?![A-Za-z0-9])", "confidence": 70,
    "examples": ["ABCD_2345", {"lines": ["Username", "ABCD_2345"], "expect": "ABCD_2345"}],
    "counter_examples": ["ABCD_2345ABCD_2345"],
}


# The page's own client GSTIN, read by a spec of this test's own so the tests do not depend on how the
# built-in specs find it.
GSTIN_SPEC = {
    "name": "test.client_gstin", "field": "gstin", "portals": [GST], "labels": ["GSTIN"], "within": 1,
    "pattern": "(?<![A-Z0-9])([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])(?![A-Z0-9])",
    "checks": ["gstin_checksum"], "confidence": 90,
    "examples": [{"lines": ["GSTIN", "19ABCPD1234E1ZB"], "expect": "19ABCPD1234E1ZB"}],
    "counter_examples": ["19ABCPD1234E1ZC"],
}


class FakeDb:
    def __init__(self, owners, values, services=None):
        self.owners, self.values = owners, values
        self.services = services if services is not None else [{"name": "GST Login", "userid_column_id": 7}]

    def get_services(self):
        return self.services

    def find_client_ids_by_column_value(self, column_id, value):
        return list(self.owners.get((column_id, value.strip().upper()), []))

    def get_client(self, client_id):
        return {"id": client_id, "values": self.values[client_id]}


class TestClientLink:
    def test_the_owner_gives_gstin_and_pan(self):
        db = FakeDb({(7, "ABCD_2345"): [1]}, {1: {7: "abcd_2345", 3: "19ABCPD1234E1ZB", 4: "Ashok"}})
        assert ClientLink(db)(GST, " abcd_2345 ") == {"gstin": "19ABCPD1234E1ZB", "pan": "ABCPD1234E", "client_id": 1}

    def test_nobody_or_two_owners_links_nothing(self):
        assert ClientLink(FakeDb({}, {}))(GST, "ABCD_2345") is None
        db = FakeDb({(7, "ABCD_2345"): [1, 2]}, {1: {3: "19ABCPD1234E1ZB"}, 2: {3: "27AAACZ9876K1ZE"}})
        assert ClientLink(db)(GST, "ABCD_2345") is None

    def test_several_registrations_give_the_pan_only(self):
        db = FakeDb({(7, "ABCD_2345"): [1]}, {1: {3: "19ABCPD1234E1ZB", 5: "27ABCPD1234E1ZA"}})
        got = ClientLink(db)(GST, "ABCD_2345")
        assert got["gstin"] == "" and got["pan"] == "ABCPD1234E"

    def test_other_portals_and_other_services_are_not_looked_at(self):
        db = FakeDb({(7, "ABCD_2345"): [1]}, {1: {3: "19ABCPD1234E1ZB"}})
        assert ClientLink(db)("Income Tax", "ABCD_2345") is None
        db.services = [{"name": "TDS", "userid_column_id": 7}]
        assert ClientLink(db)(GST, "ABCD_2345") is None

    def test_a_database_error_links_nothing(self):
        class Broken(FakeDb):
            def get_services(self):
                raise RuntimeError("locked")
        assert ClientLink(Broken({}, {}))(GST, "ABCD_2345") is None


class Rig:
    def __init__(self, tmp_path, lookup):
        extra = tmp_path / "sdis_fields.json"
        extra.write_text(json.dumps({"profile": [USERNAME_SPEC, GSTIN_SPEC]}), encoding="utf-8")
        self.page, self.calls, self.echoes, self.shade = [], [], [], 10

        def resolve(portal, user_id):
            self.calls.append((portal, user_id))
            return lookup

        self.sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH, extra], log=lambda m: None),
                             read_uia=lambda h: {"lines": list(self.page)}, log_dir=tmp_path / "shadow",
                             echo=self.echoes.append, mode="live", today=lambda: date(2026, 10, 7),
                             resolve_client=resolve)

    def see(self, lines, url=URL):
        self.page, self.shade = lines, self.shade + 37
        self.sgt.observe(1, GST, url, frame=Image.new("RGB", (64, 64), (self.shade % 250,) * 3))
        return next(iter(self.sgt._sessions.values()))


class TestSessionIdentity:
    def test_the_user_id_names_the_client_when_no_gstin_is_shown(self, tmp_path):
        r = Rig(tmp_path, {"gstin": "19ABCPD1234E1ZB", "pan": "ABCPD1234E"})
        s = r.see(["Welcome", "Username", "ABCD_2345"])
        assert r.calls == [(GST, "ABCD_2345")]
        assert s.profile["gstin"]["value"] == "19ABCPD1234E1ZB" and s.profile["gstin"]["spec"] == CLIENT_LINK_SPEC
        assert s.confirmed and s.confirm_note == "portal user ID matched a saved client"

    def test_it_is_asked_once_per_user_id(self, tmp_path):
        r = Rig(tmp_path, None)
        r.see(["Welcome", "Username", "ABCD_2345"])
        r.see(["Something else", "Welcome", "Username", "ABCD_2345"])
        assert len(r.calls) == 1

    def test_a_gstin_on_the_page_means_no_lookup(self, tmp_path):
        r = Rig(tmp_path, {"gstin": "27AAACZ9876K1ZE", "pan": "AAACZ9876K"})
        s = r.see(["GSTIN", "19ABCPD1234E1ZB", "Username", "ABCD_2345"])
        assert r.calls == [] and s.profile["gstin"]["value"] == "19ABCPD1234E1ZB"

    def test_a_gstin_that_disagrees_later_ends_the_session(self, tmp_path):
        r = Rig(tmp_path, {"gstin": "27AAACZ9876K1ZE", "pan": "AAACZ9876K"})
        first = r.see(["Welcome", "Username", "ABCD_2345"])
        second = r.see(["GSTIN", "19ABCPD1234E1ZB", "Dashboard", "Returns"], "https://return.gst.gov.in/returns/auth/dashboard")
        assert first is not second and second.strict and "gstin" not in second.profile

    def test_a_user_id_nobody_owns_leaves_the_session_unnamed(self, tmp_path):
        r = Rig(tmp_path, None)
        s = r.see(["Welcome", "Username", "ABCD_2345"])
        assert "gstin" not in s.profile and not s.confirmed
