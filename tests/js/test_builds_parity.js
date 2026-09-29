// W4-R review guards: both builds behave the same for SMTI, injected page functions do not rely
// on background-only globals, and no desktop/runtime message (which can carry a password) is
// logged outside a debug gate.
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ROOT = path.join(__dirname, '..', '..');
const BUILDS = ['sera_extension', 'sera_extension_firefox'];
const src = {};
for (const b of BUILDS) src[b] = fs.readFileSync(path.join(ROOT, b, 'background.js'), 'utf8').replace(/\r\n/g, '\n');

function fn(text, name) {
  const m = new RegExp(`\\n(async )?function ${name}\\(`).exec(text);
  assert(m, name + ' not found');
  const start = m.index;
  const end = text.indexOf('\n}\n', start);
  return text.slice(start + 1, end + 2);
}

let n = 0;
const ok = (msg) => { n++; console.log('ok  ' + msg); };

// 1. The SMTI widget is the same code in both builds.
assert.strictEqual(fn(src.sera_extension, 'manualAssistWidget'), fn(src.sera_extension_firefox, 'manualAssistWidget'));
ok('manualAssistWidget is identical in both builds');

for (const b of BUILDS) {
  // 2. Functions injected into pages run without the background's globals.
  for (const name of ['fillCredentialsInPage', 'manualAssistWidget', 'mecpWidget', '_seesPasswordBox']) {
    const body = fn(src[b], name);
    for (const g of ['SERA_DEBUG', 'SMTI_DEBUG']) {
      if (body.includes(g)) assert(body.includes(`const ${g}`), `${b}: ${name} uses ${g} without declaring it`);
    }
    for (const g of ['_passwordStore', 'scaCoordinator', 'sendToDesktop', 'ws.']) {
      assert(!body.includes(g), `${b}: ${name} uses background-only ${g}`);
    }
  }
  ok(`${b}: injected page functions use no background-only globals`);

  // 3. A whole message / payload object is never logged, not even behind a debug gate.
  for (const line of src[b].split('\n')) {
    assert(!/console\.\w+\(.*[,(]\s*(message|msg|data|payload|attempt)\s*\)/.test(line), `${b}: message object logged: ${line.trim()}`);
  }
  ok(`${b}: no logging of message objects`);

  // 4. SMTI re-inject in SMTI's own tab and settings pulled on connect exist in both builds.
  for (const name of ['maybeReinjectManualAssist', '_rememberSmtiTab', 'syncSettingsFromDesktop', 'requestSettingsOverWS']) {
    fn(src[b], name);
  }
  assert(/_passwordStore\.remove\(\['mecpPayload', 'smtiTabIds'\]\)/.test(fn(src[b], 'handleManualAssistTab')),
    `${b}: a new SMTI launch must forget the previous SMTI tab`);
  ok(`${b}: SMTI re-inject + settings sync present`);
}
console.log(`${n}/${n} passed`);
