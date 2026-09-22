import time

import pytest

from sca_protocol import (
    MSG_SCA_ARM_REQUEST,
    SCA_PROTOCOL_VERSION,
    STATE_ARMED,
    build_arm_request,
    build_arm_service,
    generate_id,
    host_matches,
    looks_like_uid,
    normalize_uid,
)


def test_generate_id():
    cmd1, cmd2 = generate_id("cmd"), generate_id("cmd")
    assert cmd1.startswith("cmd_") and len(cmd1) == 20 and cmd1 != cmd2


def test_build_arm_request_carries_no_passwords():
    svc = build_arm_service({"id": 3, "name": "GST", "login_page_link": "https://services.gst.gov.in/services/login",
                             "password_selector": "#user_pass"}, has_password=True)
    req = build_arm_request(client_id=123, client_token="tok123", matched_uid="GST_1",
                            candidate_uids=["GST_1", "ABC"], services=[svc], ttl_ms=30000, max_uses=2)
    assert req["type"] == MSG_SCA_ARM_REQUEST and req["protocol_version"] == SCA_PROTOCOL_VERSION == 2
    arm = req["arm"]
    assert arm["schema"] == 2 and arm["client_id"] == 123 and arm["max_uses"] == 2
    assert arm["state"] == STATE_ARMED and "ABC" in arm["candidate_uids"]
    assert arm["services"][0] == {"service_id": 3, "name": "GST", "url": "https://services.gst.gov.in/services/login",
                                  "host": "services.gst.gov.in", "password_selector": "#user_pass", "has_password": True,
                                  "blocked": "", "needs_confirm": False}
    now = int(time.time() * 1000)
    assert now + 29000 < arm["expires_at"] < now + 31000
    assert "password" not in str(req).replace("password_selector", "").replace("has_password", "")


def test_an_arm_with_a_password_is_refused():
    with pytest.raises(ValueError):
        build_arm_request(client_id=1, client_token="1", matched_uid="X", candidate_uids=["X"],
                          services=[{"service_id": 1, "password": "secret"}])


def test_normalize_uid():
    assert normalize_uid("  user  name  ") == "USER NAME"
    assert normalize_uid("user\tname") == "USER NAME"
    assert normalize_uid("USER_123") == "USER_123"
    assert normalize_uid("\x00\x01test\n\r") == "TEST"
    assert normalize_uid(None) == ""
    assert normalize_uid(123) == "123"
    assert normalize_uid("  ①  ") == "1"


def test_only_id_shaped_values_trigger():
    assert looks_like_uid("ABCPD1234E") and looks_like_uid("07ABCPD1234E1Z5") and looks_like_uid("CLI-0007")
    assert not looks_like_uid("NEW DELHI") and not looks_like_uid("AB") and not looks_like_uid("")


@pytest.mark.parametrize("page,login,ok", [
    ("eportal.incometax.gov.in", "https://eportal.incometax.gov.in/iec/foservices/#/login", True),
    ("login.services.gst.gov.in", "https://services.gst.gov.in/services/login", True),
    ("services.gst.gov.in.evil.com", "https://services.gst.gov.in/services/login", False),
    ("evilservices.gst.gov.in", "https://services.gst.gov.in/services/login", False),
    ("gov.in", "https://services.gst.gov.in/services/login", False),
    ("web.whatsapp.com", "https://services.gst.gov.in/services/login", False),
    ("", "https://services.gst.gov.in/services/login", False),
])
def test_host_matches(page, login, ok):
    assert host_matches(page, login) is ok
