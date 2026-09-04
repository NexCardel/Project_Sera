import re
filepath = r'C:\Users\Nex\Downloads\Project Sera\APP\sera_extension\background.js'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Forward SCA_ERROR and SCA_FILL_RESULT
# In `chrome.runtime.onMessage.addListener`, we will catch them if they are not already.

msg_handler_start = """  if (req.type && req.type.startsWith("SCA_")) {
    // Send ACK immediately
    try {
      nativePort.postMessage({ type: "SCA_ACK", command_id: req.command_id });
    } catch(e) {}
    
    // Check dedup
    if (req.command_id) {
      if (!self.seenScaCommands) self.seenScaCommands = new Set();
      if (self.seenScaCommands.has(req.command_id)) return;
      self.seenScaCommands.add(req.command_id);
    }
    handleScaCommand(req, sender, sendResponse);
  }"""

new_msg_handler = """  if (req.type && req.type.startsWith("SCA_")) {
    // Send ACK immediately if it's a request from native host
    if (req.command_id) {
      try {
        nativePort.postMessage({ type: "SCA_ACK", command_id: req.command_id });
      } catch(e) {}
      
      // Check dedup
      if (!self.seenScaCommands) self.seenScaCommands = new Set();
      if (self.seenScaCommands.has(req.command_id)) return;
      self.seenScaCommands.add(req.command_id);
    }
    
    if (req.type === "SCA_ERROR" || req.type === "SCA_FILL_RESULT") {
      try {
        nativePort.postMessage(req);
      } catch(e) {}
      return;
    }
    
    handleScaCommand(req, sender, sendResponse);
  }"""

content = content.replace(msg_handler_start, new_msg_handler)

# State transitions forwarding
# Let's add `notifyStateChange(state)`
notify_func = """
function notifyStateChange(state) {
  try {
    if (nativePort && armedSCAPayload) {
      nativePort.postMessage({
        type: "SCA_STATE",
        arm: { ...armedSCAPayload, state: state }
      });
    } else if (nativePort) {
      nativePort.postMessage({
        type: "SCA_STATE",
        arm: { state: state }
      });
    }
  } catch(e) {}
}
"""

clear_arm_start = """function clearScaArm() {"""
clear_arm_new = notify_func + """function clearScaArm() {
  notifyStateChange("IDLE");"""
content = content.replace(clear_arm_start, clear_arm_new)

# When ARMING
arming_start = """      chrome.storage.local.set({ armedSCAPayload: armedSCAPayload });
    });
  }
}"""
arming_new = """      chrome.storage.local.set({ armedSCAPayload: armedSCAPayload });
      notifyStateChange("ARMED");
    });
  }
}"""
content = content.replace(arming_start, arming_new)

# When MATCHED / FILLING
filling_start = """        if (!matchedService) {
          console.log("Sera SCA: No attached service matches the active tab; refusing autofill.");
          return;
        }
        payload.fillInProgress = true;
        chrome.storage.local.set({ armedSCAPayload: payload });"""
filling_new = """        if (!matchedService) {
          console.log("Sera SCA: No attached service matches the active tab; refusing autofill.");
          return;
        }
        payload.fillInProgress = true;
        chrome.storage.local.set({ armedSCAPayload: payload });
        notifyStateChange("MATCHED");
        setTimeout(() => notifyStateChange("FILLING"), 300);"""
content = content.replace(filling_start, filling_new)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
