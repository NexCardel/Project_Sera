import time
from sca_protocol import (
    build_arm_request, 
    generate_id, 
    MSG_SCA_ARM_REQUEST,
    STATE_ARMED,
    normalize_uid
)

def test_generate_id():
    cmd1 = generate_id("cmd")
    cmd2 = generate_id("cmd")
    assert cmd1.startswith("cmd_")
    assert cmd2.startswith("cmd_")
    assert len(cmd1) == 20  # cmd_ + 16 hex chars
    assert cmd1 != cmd2

def test_build_arm_request():
    req = build_arm_request(
        client_id=123,
        client_token="tok123",
        matched_uid="GST_1",
        candidate_uids=["GST_1", "ABC"],
        services=[{"service_key": "gst"}],
        ttl_ms=30000,
        max_uses=2
    )
    
    assert req["type"] == MSG_SCA_ARM_REQUEST
    assert req["protocol_version"] == 1
    assert "command_id" in req
    
    arm = req["arm"]
    assert arm["schema"] == 1
    assert arm["client_id"] == 123
    assert arm["client_id_token"] == "tok123"
    assert arm["matched_uid"] == "GST_1"
    assert "ABC" in arm["candidate_uids"]
    assert arm["max_uses"] == 2
    assert arm["uses_remaining"] == 2
    assert arm["state"] == STATE_ARMED
    
    now = int(time.time() * 1000)
    assert arm["expires_at"] > now + 29000
    assert arm["expires_at"] < now + 31000
    assert arm["expiresAt"] == arm["expires_at"]

def test_normalize_uid():
    assert normalize_uid("  user  name  ") == "USER NAME"
    assert normalize_uid("user\tname") == "USER NAME"
    assert normalize_uid("USER_123") == "USER_123"
    assert normalize_uid("\x00\x01test\n\r") == "TEST"
    assert normalize_uid(None) == ""
    assert normalize_uid(123) == "123"
    assert normalize_uid("  \u2460  ") == "1"

if __name__ == "__main__":
    test_generate_id()
    test_build_arm_request()
    test_normalize_uid()
    print("All protocol tests passed!")

