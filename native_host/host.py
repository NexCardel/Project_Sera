import sys
import os
import json
import struct
import socket
import threading
import time

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'host_log.txt')
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_LOG_BACKUPS = 2

def _rotate_logs_if_needed():
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) >= MAX_LOG_SIZE:
            b2 = f"{LOG_FILE}.2"
            b1 = f"{LOG_FILE}.1"
            if os.path.exists(b2):
                try:
                    os.remove(b2)
                except OSError:
                    pass
            if os.path.exists(b1):
                try:
                    os.rename(b1, b2)
                except OSError:
                    pass
            try:
                os.rename(LOG_FILE, b1)
            except OSError:
                pass
    except Exception:
        pass

def log(msg):
    try:
        _rotate_logs_if_needed()
        with open(LOG_FILE, 'a', encoding='utf-8', errors='replace') as f:
            f.write(f"{time.time()}: {msg}\n")
    except:
        pass

if sys.platform == "win32":
    try:
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    except Exception:
        pass

IPC_PORT = 49152

def read_message():
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length or len(raw_length) < 4:
        sys.exit(0)
    message_length = struct.unpack('@I', raw_length)[0]
    message_bytes = sys.stdin.buffer.read(message_length)
    if len(message_bytes) < message_length:
        sys.exit(0)
    msg = json.loads(message_bytes.decode('utf-8'))
    log(f"read_message from browser: {msg}")
    return msg

def send_message(message_dict):
    log(f"send_message to browser: {message_dict}")
    try:
        encoded = json.dumps(message_dict).encode('utf-8')
        sys.stdout.buffer.write(struct.pack('@I', len(encoded)))
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception as e:
        log(f"send_message error: {e}")
        pass

def forward_to_app(msg):
    log(f"forward_to_app: {msg}")
    for attempt in range(3):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect(('127.0.0.1', IPC_PORT))
                s.sendall(json.dumps(msg).encode('utf-8'))
                log(f"forward_to_app success on attempt {attempt}")
                return True
        except (ConnectionRefusedError, OSError) as e:
            log(f"forward_to_app failed attempt {attempt}: {e}")
            time.sleep(0.3)
    
    send_message({"status": "disconnected", "error": "Desktop app not running"})
    return False

def listen_to_browser():
    log("listen_to_browser started")
    while True:
        try:
            msg = read_message()
            forward_to_app(msg)
        except Exception as e:
            log(f"listen_to_browser exception: {e}")
            sys.exit(0)

def listen_to_app():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    bound_port = None
    for p in range(IPC_PORT + 1, IPC_PORT + 4):
        try:
            server.bind(('127.0.0.1', p))
            server.listen(5)
            bound_port = p
            break
        except Exception:
            pass
    
    log(f"listen_to_app bound to port: {bound_port}")
    if not bound_port:
        return

    while True:
        try:
            conn, addr = server.accept()
            with conn:
                data = conn.recv(65536)
                if data:
                    msg = json.loads(data.decode('utf-8'))
                    log(f"listen_to_app received from app: {msg}")
                    send_message(msg)
        except Exception as e:
            log(f"listen_to_app error: {e}")

def main():
    log("host.py started")
    threading.Thread(target=listen_to_app, daemon=True).start()
    listen_to_browser()

if __name__ == '__main__':
    main()
