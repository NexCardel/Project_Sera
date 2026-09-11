import base64
import base64
import gzip
import json
import socket
from PySide6.QtCore import QThread, Signal, QObject

IPC_PORT = 49152


def _decode_transport_message(msg: dict) -> dict:
    """Restore a losslessly compressed assembler payload, if present."""
    if not isinstance(msg, dict) or msg.get("type") != "filing_result_compressed":
        return msg
    if msg.get("encoding") != "gzip+base64":
        return msg
    data_blob = msg.get("data")
    if not isinstance(data_blob, str):
        return msg
    decompressed = gzip.decompress(base64.b64decode(data_blob.encode("ascii")))
    restored = json.loads(decompressed.decode("utf-8"))
    if not isinstance(restored, dict):
        return msg
    if "original_type" in msg:
        restored["type"] = msg["original_type"]
    return restored

class ExtensionListener(QThread):
    filing_result_received = Signal(dict)
    uncertain_result_received = Signal(dict)
    sca_state_received = Signal(dict)
    sca_error_received = Signal(dict)
    sca_fill_result_received = Signal(dict)
    session_started_received = Signal(dict)
    scc_password_verified_received = Signal(dict)
    sdc_timeline_received = Signal(dict)
    sudr_capture_received = Signal(dict)  # SUDR canonical envelope

    def __init__(self, parent=None):
        if not isinstance(parent, QObject):
            parent = None
        super().__init__(parent)
        self._running = True
        self._server = None
        self.settings_provider = None

    def stop(self):
        self._running = False
        server = getattr(self, '_server', None)
        if server:
            try:
                # Force socket to close, immediately unblocking any pending accept() calls
                import socket
                server.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                server.close()
            except Exception:
                pass
            self._server = None
        try:
            if self.isRunning():
                self.quit()
                self.wait(2000) # Wait up to 2 seconds for loop to gracefully exit
        except RuntimeError:
            pass

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(0.3)
        self._server = server
        try:
            server.bind(('127.0.0.1', IPC_PORT))
            server.listen(10)
        except Exception as e:
            print(f"ExtensionListener failed to bind: {e}")
            return

        while self._running:
            try:
                try:
                    conn, _addr = server.accept()
                except socket.timeout:
                    continue
                except Exception:
                    break

                with conn:
                    conn.settimeout(2.0)
                    chunks = []
                    while True:
                        try:
                            part = conn.recv(65536)
                            if not part:
                                break
                            chunks.append(part)
                        except socket.timeout:
                            break
                    if chunks:
                        raw_data = b"".join(chunks).decode('utf-8', errors='ignore')
                        try:
                            # If received via HTTP POST / OPTIONS from browser fetch or local simulation harness
                            if raw_data.startswith("OPTIONS "):
                                # Respond to CORS preflight
                                response = (
                                    b"HTTP/1.1 200 OK\r\n"
                                    b"Access-Control-Allow-Origin: *\r\n"
                                    b"Access-Control-Allow-Methods: POST, GET, OPTIONS\r\n"
                                    b"Access-Control-Allow-Headers: Content-Type\r\n"
                                    b"Content-Length: 0\r\n\r\n"
                                )
                                conn.sendall(response)
                                continue

                            json_str = raw_data
                            is_http = False
                            if "HTTP/" in raw_data and ("\r\n\r\n" in raw_data or "\n\n" in raw_data):
                                is_http = True
                                json_str = raw_data.split("\r\n\r\n", 1)[-1] if "\r\n\r\n" in raw_data else raw_data.split("\n\n", 1)[-1]

                            msg = _decode_transport_message(json.loads(json_str))
                            mtype = msg.get('type')
                            if mtype in ('filing_result', 'audit_event'):
                                self.filing_result_received.emit(msg)
                            elif mtype == 'uncertain_result':
                                self.uncertain_result_received.emit(msg)
                            elif mtype == 'SCA_ACK':
                                cmd_id = msg.get('command_id')
                                if cmd_id:
                                    import automation
                                    automation.register_ack(cmd_id)
                            elif mtype == 'SCA_STATE':
                                self.sca_state_received.emit(msg)
                            elif mtype == 'SCA_ERROR':
                                self.sca_error_received.emit(msg)
                            elif mtype == 'SCA_FILL_RESULT':
                                self.sca_fill_result_received.emit(msg)
                            elif mtype == 'session_start':
                                self.session_started_received.emit(msg)
                            elif mtype == 'scc_password_verified':
                                self.scc_password_verified_received.emit(msg)
                            elif mtype == 'sdc_session_timeline':
                                self.sdc_timeline_received.emit(msg)
                            elif mtype == 'sudr_capture':
                                self.sudr_capture_received.emit(msg)

                            if mtype in ('request_settings', 'get_settings'):
                                settings_data = self.settings_provider() if callable(self.settings_provider) else {"status": "ok"}
                                resp_body = json.dumps(settings_data).encode('utf-8')
                                if is_http:
                                    header = (
                                        "HTTP/1.1 200 OK\r\n"
                                        "Access-Control-Allow-Origin: *\r\n"
                                        "Access-Control-Allow-Methods: POST, GET, OPTIONS\r\n"
                                        "Access-Control-Allow-Headers: Content-Type\r\n"
                                        "Content-Type: application/json\r\n"
                                        f"Content-Length: {len(resp_body)}\r\n\r\n"
                                    ).encode('utf-8')
                                    conn.sendall(header + resp_body)
                                else:
                                    conn.sendall(resp_body)
                                continue

                            if is_http:
                                resp = (
                                    b"HTTP/1.1 200 OK\r\n"
                                    b"Access-Control-Allow-Origin: *\r\n"
                                    b"Content-Type: application/json\r\n"
                                    b"Content-Length: 15\r\n\r\n"
                                    b'{"status":"ok"}'
                                )
                                conn.sendall(resp)
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                if self._running:
                    print(f"ExtensionListener error: {e}")

        try:
            server.close()
        except Exception:
            pass

    def __del__(self):
        try:
            self.stop()
        except RuntimeError:
            pass
