import time
import pytest
from unittest.mock import MagicMock
from main import SeraApp

class FakeSyncService:
    def __init__(self):
        self.push_to_calls = 0
        self.request_pull_calls = 0
        self.broadcast_tracker_calls = 0
        self.broadcast_audit_calls = 0

    def get_peers(self):
        return [{"ip": "192.168.1.5", "host": "Test-PC"}]

    def push_to(self, *args, **kwargs):
        self.push_to_calls += 1

    def request_pull_from(self, *args, **kwargs):
        self.request_pull_calls += 1

    def broadcast_tracker_dumps(self, *args, **kwargs):
        self.broadcast_tracker_calls += 1

    def broadcast_audit_logs(self, *args, **kwargs):
        self.broadcast_audit_calls += 1

class DummyApp:
    _telemetry_delay = 0.5
    
    def __init__(self):
        import threading
        self.sync_service = FakeSyncService()
        self.db = MagicMock()
        self.db.get_tracker_dumps.return_value = [{"dump": "1"}]
        self.db.get_audit_logs.return_value = [{"log": "1"}]
        self._telemetry_lock = threading.Lock()
        self._telemetry_timer = None
        self._last_telemetry_time = 0.0

def test_write_does_not_push_database():
    win = DummyApp()
    
    # Track call times to verify the gap
    call_times = []
    original_broadcast = win.sync_service.broadcast_tracker_dumps
    def tracking_broadcast(*args, **kwargs):
        call_times.append(time.monotonic())
        original_broadcast(*args, **kwargs)
    win.sync_service.broadcast_tracker_dumps = tracking_broadcast

    # Bind the method from SeraApp to our dummy instance
    method = SeraApp._broadcast_live_update_to_peers.__get__(win, DummyApp)
    
    # Fire 10 calls instantly
    for _ in range(10):
        method()
        
    # Wait for the first immediate thread to run
    time.sleep(0.1)
    
    # Verify that push_to and request_pull_from were NEVER called
    assert win.sync_service.push_to_calls == 0
    assert win.sync_service.request_pull_calls == 0
    
    # Verify the first call fired immediately
    assert win.sync_service.broadcast_tracker_calls == 1
    
    # Wait for the debounce timer to end the first window
    time.sleep(0.5)
    
    # The debounced run should have fired now
    assert win.sync_service.broadcast_tracker_calls == 2
    
    # If we fire again right after the timer, it should NOT fire immediately 
    # since the window was just reset by the timer run setting _last_telemetry_time.
    method()
    time.sleep(0.1)
    assert win.sync_service.broadcast_tracker_calls == 2
    
    # Fire more calls in this new window
    for _ in range(5):
        method()
        
    # End of second window
    time.sleep(0.5)
    assert win.sync_service.broadcast_tracker_calls == 3
    assert win.sync_service.broadcast_audit_calls == 3
    
    # Check that no two sends are closer than the window (with small slack)
    for i in range(1, len(call_times)):
        assert call_times[i] - call_times[i-1] >= DummyApp._telemetry_delay - 0.05
