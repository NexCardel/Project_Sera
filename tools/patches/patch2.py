import os
import sys

def patch_itr_protocol_again():
    filepath = r'c:\Users\Nex\Downloads\Project Sera\APP\sera_extension\sdc\protocols\itr_protocol.js'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the target function
    target_start = "    async function _handleLoginLogout(url) {"
    target_end_marker = "    // ─── Register SDC.onSessionClear for ITR"
    
    if target_start not in content or target_end_marker not in content:
        print("Could not find the function to replace.")
        return
        
    start_idx = content.find(target_start)
    end_idx = content.find(target_end_marker)
    
    new_func = '''    async function _handleLoginLogout(url) {
      const lower = (url || '').toLowerCase();
      const isLogout = lower.includes('logout') || lower.includes('signout') || lower.includes('sign-out') ||
          lower.includes('sessionexpire') || lower.includes('session-expire') || lower.includes('sessionexpired') ||
          lower.includes('session-expired') || lower.includes('timeout');

      if (isLogout) {
        await SDC.session.finalizeLogout(url);
        _resetItrSession();
        await SDC.clearAllSessions();
        return null;
      }

      // If it is a login/password page, capture PAN if visible
      let pan = '';
      const entityDivs = document.querySelectorAll('div.entity');
      for (const div of entityDivs) {
        if (div.textContent.includes('PAN')) {
          const boldSpan = div.querySelector('span.boldfont');
          if (boldSpan) {
            pan = boldSpan.textContent.trim();
          } else {
            const match = div.textContent.match(/PAN\\s*:\\s*([A-Z]{5}[0-9]{4}[A-Z]{1})/i);
            if (match) pan = match[1].trim();
          }
        }
      }

      // Generic PAN regex scan fallback (replaces hardcoded input ID)
      if (!pan) {
         const panRegex = /^[A-Z]{5}[0-9]{4}[A-Z]{1}$/i;
         const boundaryRegex = /\\b([A-Z]{5}[0-9]{4}[A-Z]{1})\\b/i;
         
         // 1. Scan all input values generically
         const inputs = document.querySelectorAll('input');
         for (const input of inputs) {
           const val = (input.value || '').trim();
           if (panRegex.test(val)) {
             pan = val.toUpperCase();
             break;
           }
         }
         
         // 2. Scan visible text body as absolute last resort
         if (!pan && document.body && document.body.innerText) {
           const match = document.body.innerText.match(boundaryRegex);
           if (match) {
             pan = match[1].toUpperCase();
           }
         }
      }

      if (pan) {
        return {
          pan: pan,
          client_name: '',
          client_temp_name: '',
          dob: '',
          form: '',
          ay: '',
          status: 'Pre-Login / Password'
        };
      }
      return null;
    }

'''
    
    new_content = content[:start_idx] + new_func + content[end_idx:]
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    paths_to_sync = [
        r'C:\Users\Nex\AmanAssociates_Sera\sera_extension\sdc\protocols\itr_protocol.js',
        r'C:\Users\Nex\Downloads\Project Sera\APP\sera_extension_firefox\sdc\protocols\itr_protocol.js',
        r'C:\Users\Nex\Downloads\Project Sera\source_2\sera_extension\sdc\protocols\itr_protocol.js'
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

patch_itr_protocol_again()
