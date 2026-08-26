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
                        raw_data = b"".join(chunks).decode('utf-8')
                        try:
                            msg = json.loads(raw_data)
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
                                # Phase 1: We receive state, we can log it for now
                                print(f"SCA State Sync: {msg.get('arm', {}).get('state')} - client {msg.get('arm', {}).get('client_id')}")
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
