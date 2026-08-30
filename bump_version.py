import os
import json

def bump_versions(new_version="2.9.0"):
    # 1. Update version.py
    v_py = r'c:\Users\Nex\Downloads\Project Sera\APP\version.py'
    with open(v_py, 'r', encoding='utf-8') as f:
        v_py_content = f.read()
    import re
    v_py_content = re.sub(r'APP_VERSION\s*=\s*".*?"', f'APP_VERSION = "{new_version}"', v_py_content)
    with open(v_py, 'w', encoding='utf-8') as f:
        f.write(v_py_content)
        
    # 2. Update installer_setup.iss
    iss = r'c:\Users\Nex\Downloads\Project Sera\APP\build_tools\installer_setup.iss'
    with open(iss, 'r', encoding='utf-8') as f:
        iss_content = f.read()
    iss_content = re.sub(r'#define MyAppVersion\s+".*?"', f'#define MyAppVersion "{new_version}"', iss_content)
    with open(iss, 'w', encoding='utf-8') as f:
        f.write(iss_content)
        
    # 3. Update manifest.json
    manifest = r'c:\Users\Nex\Downloads\Project Sera\APP\sera_extension\manifest.json'
    with open(manifest, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['version'] = new_version
    with open(manifest, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    # Sync manifest to other folders
    paths_to_sync = [
        r'C:\Users\Nex\AmanAssociates_Sera\sera_extension\manifest.json',
        r'C:\Users\Nex\Downloads\Project Sera\APP\sera_extension_firefox\manifest.json',
        r'C:\Users\Nex\Downloads\Project Sera\source_2\sera_extension\manifest.json'
    ]
    import shutil
    for p in paths_to_sync:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            shutil.copy2(manifest, p)
        except Exception:
            pass
            
    print(f"All versions synchronized to {new_version}")

bump_versions("2.9.0")
