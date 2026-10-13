"""SDIS Part T: portals registered from service login links, and the containers file loader."""

import json

import pytest

from core.sdis import config, portals
from core.vsdc import vsdc_scope as scope


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "office_sdis_containers.json"))
    monkeypatch.setenv(scope.SCOPE_CONFIG_ENV, str(tmp_path / "no_vsdc_scope.json"))
    monkeypatch.delenv("VSDC_ALLOW_LOCAL_TEST", raising=False)
    config._current = None
    scope.set_services_source(None)
    scope.reload_extra_domains()
    yield
    config._current = None
    scope.set_services_source(None)
    scope.reload_extra_domains()


def _office(tmp_path, data):
    (tmp_path / "office_sdis_containers.json").write_text(json.dumps(data), encoding="utf-8")
    config.reload()
    scope.reload_extra_domains()


def _services(rows):
    scope.set_services_source(lambda: list(rows))


@pytest.mark.parametrize("host,dom", [
    ("services.gst.gov.in", "gst.gov.in"),
    ("eportal.incometax.gov.in", "incometax.gov.in"),
    ("unifiedportal-mem.epfindia.gov.in", "epfindia.gov.in"),
    ("www.mca.gov.in", "mca.gov.in"),
    ("a.b.example.co.in", "example.co.in"),
    ("login.example.com", "example.com"),
    ("incometax.gov.in.evil.example", "incometax.gov.in.evil.example"),
    ("gov.in", None),
    ("in", None),
    ("localhost", None),
    ("192.168.1.10", None),
])
def test_registered_domain(host, dom):
    assert scope.registered_domain(host) == dom


def test_ip_only_with_local_test_pages(monkeypatch):
    monkeypatch.setenv("VSDC_ALLOW_LOCAL_TEST", "1")
    assert scope.registered_domain("127.0.0.2") == "127.0.0.2"
    assert scope.registered_domain("localhost") is None


def test_service_link_puts_domain_in_scope():
    _services([{"name": "EPF", "login_page_link": "https://unifiedportal-mem.epfindia.gov.in/memberinterface/"}])
    assert scope.portal_for_url("https://passbook.epfindia.gov.in/x") == "EPF"
    assert scope.is_in_scope_url("epfindia.gov.in/login")
    assert not scope.is_in_scope_url("https://epfindia.gov.in.evil.example/")
    assert not scope.is_in_scope_url("https://notepfindia.gov.in/")
    assert not scope.is_in_scope_url("https://www.google.com/search?q=epfindia.gov.in")
    assert not scope.is_government_registry_host("https://passbook.epfindia.gov.in/")
    assert [p["name"] for p in portals.registered_portals()] == ["Income Tax", "GST Portal", "EPF"]
    assert portals.registered_portals()[2] == {"name": "EPF", "domains": ("epfindia.gov.in",), "source": "service"}


def test_builtin_wins_and_lookalike_stays_out():
    _services([{"name": "My GST", "login_page_link": "https://services.gst.gov.in/services/login"},
               {"name": "Evil", "login_page_link": "https://incometax.gov.in.evil.example/login"}])
    assert scope.portal_for_url("https://services.gst.gov.in/x") == "GST Portal"
    assert scope.portal_for_url("https://eportal.incometax.gov.in/") == "Income Tax"
    assert scope.service_domains() == {"Evil": ("incometax.gov.in.evil.example",)}
    assert scope.portal_for_url("https://incometax.gov.in.evil.example/a") == "Evil"


def test_bad_links_register_nothing():
    _services([{"name": "A", "login_page_link": "https://gov.in/"},
               {"name": "B", "login_page_link": "http://localhost:8080/"},
               {"name": "C", "login_page_link": ""},
               {"name": "D", "login_page_link": "not a link at all"}])
    assert scope.service_domains() == {}


def test_never_register_refuses(tmp_path):
    _office(tmp_path, {"version": 1, "portal_exceptions": {"never_register": ["login.microsoftonline.com"]}})
    _services([{"name": "Office", "login_page_link": "https://login.microsoftonline.com/common"}])
    assert scope.never_registered("microsoftonline.com")
    assert scope.service_domains() == {}
    assert not scope.is_in_scope_url("https://login.microsoftonline.com/")


def test_extra_domains_add(tmp_path):
    _office(tmp_path, {"version": 1, "portal_exceptions": {"extra_domains": {"EPF": ["work.example.gov.in"],
                                                                             "Unknown": ["other.example.gov.in"]}}})
    _services([{"name": "EPF", "login_page_link": "https://unifiedportal-mem.epfindia.gov.in/"}])
    assert scope.portal_for_url("https://work.example.gov.in/page") == "EPF"
    assert not scope.is_in_scope_url("https://other.example.gov.in/")       # not a registered portal


def test_removing_service_takes_it_out():
    rows = [{"name": "EPF", "login_page_link": "https://unifiedportal-mem.epfindia.gov.in/"}]
    _services(rows)
    assert scope.is_in_scope_url("https://unifiedportal-mem.epfindia.gov.in/")
    rows.clear()
    assert scope.is_in_scope_url("https://unifiedportal-mem.epfindia.gov.in/")   # cached until reload
    scope.reload_extra_domains()
    assert not scope.is_in_scope_url("https://unifiedportal-mem.epfindia.gov.in/")
    assert portals.portal_names() == ["Income Tax", "GST Portal"]


def test_no_services_no_change():
    assert scope.service_domains() == {}
    assert scope.portal_for_url("https://unifiedportal-mem.epfindia.gov.in/") is None


def test_builtin_config_is_empty_and_valid():
    cfg, errors = config.load([config.BUILTIN_PATH])
    assert errors == [] and cfg == config.empty()


@pytest.mark.parametrize("bad", [
    "{not json",
    json.dumps([1]),
    json.dumps({"version": 2}),
    json.dumps({"version": True}),
    json.dumps({"version": 1, "portal_exceptions": {"never_register": ["gov.in"]}}),
    json.dumps({"version": 1, "portal_exceptions": {"never_register": ["https://x.example.com/"]}}),
    json.dumps({"version": 1, "portal_exceptions": {"extra_domains": {"EPF": ["localhost"]}}}),
    json.dumps({"version": 1, "portal_exceptions": {"extra_domains": {"EPF": "a.example.gov.in"}}}),
    json.dumps({"version": 1, "portal_exceptions": {"extra_domain": {}}}),
])
def test_broken_file_refused_previous_kept(tmp_path, bad):
    _office(tmp_path, {"version": 1, "portal_exceptions": {"never_register": ["login.microsoftonline.com"]}})
    assert config.portal_exceptions()["never_register"] == ["login.microsoftonline.com"]
    (tmp_path / "office_sdis_containers.json").write_text(bad, encoding="utf-8")
    cfg, errors = config.reload()
    assert len(errors) == 1 and "refused" in errors[0]
    assert config.portal_exceptions()["never_register"] == ["login.microsoftonline.com"]


def test_broken_file_first_load_falls_back_to_builtin(tmp_path):
    (tmp_path / "office_sdis_containers.json").write_text(json.dumps({"version": 0}), encoding="utf-8")
    cfg, errors = config.load()
    assert cfg == config.empty() and len(errors) == 1


def test_unchecked_sections_pass_through(tmp_path):
    _office(tmp_path, {"version": 1, "others": {"EPF": ["x"]}, "unknown_section": 1})
    assert config.current()["others"] == {"EPF": ["x"]}
    assert "unknown_section" not in config.current()
