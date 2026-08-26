import re
filepath = r'C:\Users\Nex\Downloads\Project Sera\APP\automation.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

target = """            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect(('127.0.0.1', 49153))
                    s.sendall(json.dumps(payload).encode('utf-8'))
                    if cmd_id == "legacy":
                        return # Fire and forget for legacy
            except Exception:
                pass"""

replacement = """            try:
                for p in range(49153, 49162):
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.2)
                            s.connect(('127.0.0.1', p))
                            s.sendall(json.dumps(payload).encode('utf-8'))
                    except Exception:
                        pass
                if cmd_id == "legacy":
                    return # Fire and forget for legacy
            except Exception:
                pass"""

content = content.replace(target, replacement)
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
