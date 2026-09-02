import os
import re
import json
import shutil

def bump_versions(new_version="2.9.4"):
    # 1. Update version.py
    for v_py in [r'version.py', r'source_2\version.py']:
        if os.path.exists(v_py):
            with open(v_py, 'r', encoding='utf-8') as f:
                c = f.read()
            c = re.sub(r'APP_VERSION\s*=\s*".*?"', f'APP_VERSION = "{new_version}"', c)
            with open(v_py, 'w', encoding='utf-8') as f:
                f.write(c)
            print(f"Updated {v_py}")

    # 2. Update installer_setup.iss
    iss = r'build_tools\installer_setup.iss'
    if os.path.exists(iss):
        with open(iss, 'r', encoding='utf-8') as f:
            c = f.read()
        c = re.sub(r'#define MyAppVersion\s+".*?"', f'#define MyAppVersion "{new_version}"', c)
        with open(iss, 'w', encoding='utf-8') as f:
            f.write(c)
        print(f"Updated {iss}")

    # 3. Update net_interceptor.js (Sera SAD internal engine version)
    net_paths = [
        r'sera_extension\content_scripts\net_interceptor.js',
        r'sera_extension_firefox\content_scripts\net_interceptor.js',
        r'source_2\sera_extension\content_scripts\net_interceptor.js',
        r'source_2\sera_extension_firefox\content_scripts\net_interceptor.js',
    ]
    for p in net_paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                c = f.read()
            c = re.sub(r'const SAD_VERSION\s*=\s*".*?";', f'const SAD_VERSION = "{new_version}";', c)
            with open(p, 'w', encoding='utf-8') as f:
                f.write(c)
            print(f"Updated {p}")

    # 4. Update manifest.json
    manifest_paths = [
        r'sera_extension\manifest.json',
        r'sera_extension_firefox\manifest.json',
        r'source_2\sera_extension\manifest.json',
        r'source_2\sera_extension_firefox\manifest.json',
    ]
    for p in manifest_paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['version'] = new_version
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            print(f"Updated {p}")

    # 5. Update docs
    for doc in [r'docs\Blueprints\SDC_Blueprint.md', r'docs\browser-automation-extension.md']:
        if os.path.exists(doc):
            with open(doc, 'r', encoding='utf-8') as f:
                c = f.read()
            c = re.sub(r'v2\.9\.\d+', f'v{new_version}', c)
            c = re.sub(r'2\.9\.\d+', new_version, c)
            with open(doc, 'w', encoding='utf-8') as f:
                f.write(c)
            print(f"Updated {doc}")

    # 6. Synchronize extension to live directory C:\Users\Nex\AmanAssociates_Sera\sera_extension
    live_ext = r'C:\Users\Nex\AmanAssociates_Sera\sera_extension'
    if os.path.exists(live_ext):
        for root, dirs, files in os.walk(r'sera_extension'):
            rel = os.path.relpath(root, r'sera_extension')
            dest_dir = os.path.join(live_ext, rel) if rel != '.' else live_ext
            os.makedirs(dest_dir, exist_ok=True)
            for f in files:
                src_f = os.path.join(root, f)
                dest_f = os.path.join(dest_dir, f)
                shutil.copy2(src_f, dest_f)
        print("Synchronized all extension files to live AmanAssociates_Sera directory.")

    print(f"All versions successfully bumped to {new_version}")

if __name__ == '__main__':
    bump_versions("2.9.4")
