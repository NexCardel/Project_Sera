import re
filepath = r'C:\Users\Nex\Downloads\Project Sera\APP\sera_extension\content_scripts\login.js'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old_fill = """    let attempts = 0;
    const interval = setInterval(() => {
      attempts++;
      const pwFields = adapter.findPasswordFields(document);
      
      if (pwFields.length > 0) {
        clearInterval(interval);
        setTimeout(() => {
          const success = adapter.fillPassword(pwFields[0], password);
          if (success) {
            console.log("Sera SCA: Password filled via adapter.");
            showScaToast(message.business_name, message.owner_name, message.portal_name);
            chrome.runtime.sendMessage({ type: "sca_fill_completed" });
          }
        }, 100);
      } else if (attempts >= 200) {
        clearInterval(interval); // Timeout after ~30 seconds (200 * 150ms)
      }
    }, 150);"""

new_fill = """    let attempts = 0;
    const interval = setInterval(() => {
      attempts++;
      try {
        const pwFields = adapter.findPasswordFields(document);
        
        if (pwFields.length > 0) {
          clearInterval(interval);
          setTimeout(() => {
            try {
              const success = adapter.fillPassword(pwFields[0], password);
              if (success) {
                console.log("Sera SCA: Password filled via adapter.");
                showScaToast(message.business_name, message.owner_name, message.portal_name);
                chrome.runtime.sendMessage({ type: "SCA_FILL_RESULT", result: "success", detail: `Password filled on ${message.adapter}` });
                chrome.runtime.sendMessage({ type: "sca_fill_completed" });
              } else {
                chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Adapter ${message.adapter} failed to fill password field.` });
              }
            } catch (fillErr) {
              chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Exception during ${message.adapter} fill: ${fillErr.message}` });
            }
          }, 100);
        } else if (attempts >= 200) {
          clearInterval(interval);
          chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Timeout waiting for password field on ${message.adapter}` });
        }
      } catch (err) {
        clearInterval(interval);
        chrome.runtime.sendMessage({ type: "SCA_ERROR", detail: `Exception detecting fields on ${message.adapter}: ${err.message}` });
      }
    }, 150);"""

content = content.replace(old_fill, new_fill)

content = re.sub(r'(function showScaToast\(.*?\}\s*){2,}', r'\1', content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
