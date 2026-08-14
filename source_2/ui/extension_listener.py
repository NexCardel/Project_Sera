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

    def stop(self):
        self._running = False
        if self.isRunning():
            self.quit()
            self.wait(1500)

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(1.0)
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
                    conn.settimeout(1.0)
                    data = conn.recv(4096)
                    if data:
                        msg = json.loads(data.decode('utf-8'))
                        if msg.get('type') == 'filing_result':
                            self.filing_result_received.emit(msg)
                        elif msg.get('type') == 'uncertain_result':
                            self.uncertain_result_received.emit(msg)
            except Exception as e:
                if self._running:
                    print(f"ExtensionListener error: {e}")

        try:
            server.close()
        except Exception:
            pass

    def __del__(self):
        self.stop()

