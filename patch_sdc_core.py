import os
import sys

def patch_sdc_core():
    filepath = r'c:\Users\Nex\Downloads\Project Sera\APP\sera_extension\sdc\sdc_core.js'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the target block 1
    target1 = '''        if (matchedCrosshair.id === 'itr_login') {
          // Auth / logout route: session was finalized & wiped by handler — skip save
          return;
        }'''
        
    replacement1 = '''        // Handle itr_login explicitly so we don't abort on PAN captures
        if (matchedCrosshair.id === 'itr_login' && !capture) {
            const urlL = (url || '').toLowerCase();
            const isLogout = urlL.includes('logout') || urlL.includes('signout') || urlL.includes('sign-out') || 
                             urlL.includes('sessionexpire') || urlL.includes('session-expire') || 
                             urlL.includes('sessionexpired') || urlL.includes('session-expired') || urlL.includes('timeout');
            if (isLogout) {
                return; // Auth / logout route: session was finalized & wiped by handler — skip save
            }
        }'''
        
    if target1 in content:
        content = content.replace(target1, replacement1)
    else:
        print("Target 1 not found.")
        return
        
    # Find the target block 2
    target2 = "} else if (matchedCrosshair.id !== 'itr_login' && retryCount < 2) {"
    replacement2 = "} else if (retryCount < 2) {"
    
    if target2 in content:
        content = content.replace(target2, replacement2)
    else:
        print("Target 2 not found.")
        return
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
        
    paths_to_sync = [
        r'C:\Users\Nex\AmanAssociates_Sera\sera_extension\sdc\sdc_core.js',
        r'C:\Users\Nex\Downloads\Project Sera\APP\sera_extension_firefox\sdc\sdc_core.js',
        r'C:\Users\Nex\Downloads\Project Sera\source_2\sera_extension\sdc\sdc_core.js'
    ]
    
    import shutil
    for p in paths_to_sync:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            shutil.copy2(filepath, p)
            print(f"Synced to {p}")
        except Exception as e:
            print(f"Failed to sync to {p}: {e}")

    print("Success")

patch_sdc_core()
