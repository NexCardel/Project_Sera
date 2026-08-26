import re
filepath = r'C:\Users\Nex\Downloads\Project Sera\APP\sera_extension\background.js'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

target = """    nativePort.onMessage.addListener((message) => {
      console.log("Received from Sera desktop:", message);
      if (message.type === "autofill" && message.url) {"""

replacement = """    nativePort.onMessage.addListener((message) => {
      console.log("Received from Sera desktop:", message);
      if (message.type && message.type.startsWith("SCA_")) {
        if (message.command_id) {
          try {
            nativePort.postMessage({ type: "SCA_ACK", command_id: message.command_id });
          } catch(e) {}
          if (!self.seenScaCommands) self.seenScaCommands = new Set();
          if (self.seenScaCommands.has(message.command_id)) return;
          self.seenScaCommands.add(message.command_id);
        }
        handleScaCommand(message, {id: "nativeHost"}, () => {});
        return;
      }
      if (message.type === "autofill" && message.url) {"""

content = content.replace(target, replacement)

# We should also clean up handleScaCommand to remove the chrome.storage.local.get that handles SCA_ARM natively since it was duplicated.
# Wait, handleScaCommand handles SCA_ARM_REQUEST.

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
