import sys
import os
import json
import struct
import socket
import threading
import time

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
    return json.loads(message_bytes.decode('utf-8'))

def send_message(message_dict):
    try:
        encoded = json.dumps(message_dict).encode('utf-8')
        sys.stdout.buffer.write(struct.pack('@I', len(encoded)))
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception:
        pass

def forward_to_app(msg):
    for attempt in range(3):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect(('127.0.0.1', IPC_PORT))
                s.sendall(json.dumps(msg).encode('utf-8'))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    
    send_message({"status": "disconnected", "error": "Desktop app not running"})
    return False

def listen_to_browser():
    while True:
        try:
            msg = read_message()
            forward_to_app(msg)
        except Exception:
            sys.exit(0)

def listen_to_app():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(('127.0.0.1', IPC_PORT + 1))
        server.listen(5)
    except Exception:
        return

    while True:
        try:
            conn, addr = server.accept()
            with conn:
                data = conn.recv(65536)
                if data:
                    msg = json.loads(data.decode('utf-8'))
                    send_message(msg)
        except Exception:
            pass

def main():
    threading.Thread(target=listen_to_app, daemon=True).start()
    listen_to_browser()

if __name__ == '__main__':
    main()
