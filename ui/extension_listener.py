import json
import socket

from PySide6.QtCore import QThread, Signal

IPC_PORT = 49152

class ExtensionListener(QThread):
    filing_result_received = Signal(dict)
    uncertain_result_received = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._server = None

    def stop(self):
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        try:
            if self.isRunning():
                self.quit()
                self.wait(150)
        except RuntimeError:
            # C++ object already deleted by Qt — nothing to do.
            pass

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(0.3)
        self._server = server
        try:
            server.bind(('127.0.0.1', IPC_PORT))
            server.listen(5)
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
                        raw_data = b"".join(chunks).decode('utf-8')
                        try:
                            msg = json.loads(raw_data)
                            print(f"[ExtensionListener] Received message: {msg.get('type')} (ARN: {msg.get('arn', 'N/A')})")
                            if msg.get('type') == 'filing_result':
                                self.filing_result_received.emit(msg)
                            elif msg.get('type') == 'uncertain_result':
                                self.uncertain_result_received.emit(msg)
                            elif msg.get('type') == 'audit_event':
                                self.filing_result_received.emit(msg)
                        except json.JSONDecodeError as jde:
                            print(f"[ExtensionListener] JSON Decode Error: {jde}")
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
            # Qt has already destroyed the underlying C++ object; ignore.
            pass

