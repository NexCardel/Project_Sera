// SCA scope (D7): login.js / sca_adapters.js are registered only for the approved portal hosts.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.join(__dirname, '..', '..');
let failures = 0;
function check(name, fn) {
  return Promise.resolve().then(fn).then(() => console.log('ok  ' + name), e => { failures++; console.log('FAIL ' + name + ': ' + e.message); });
}

function load(build, stored, api) {
  const src = fs.readFileSync(path.join(ROOT, build, 'background.js'), 'utf8');
  const start = src.indexOf('const SCA_SCRIPT_ID');
  const end = src.indexOf('applyScaScope();');
  assert(start > 0 && end > start, 'scope block not found');
  const listeners = [];
  const chrome = {
    storage: { local: { get: async () => ({ allowedDomains: stored.value }) }, onChanged: { addListener: f => listeners.push(f) } },
    scripting: api.scripting,
  };
  const ctx = { chrome, browser: api.browser, console, String, Set, Promise };
  vm.createContext(ctx);
  vm.runInContext(src.slice(start, end) + '\nthis.applyScaScope = applyScaScope; this.scaScopeMatches = scaScopeMatches;', ctx);
  return ctx;
}

function scriptingApi() {
  const calls = { reg: [], unreg: [] };
  return { calls, scripting: {
    registerContentScripts: async s => { calls.reg.push(s); },
    unregisterContentScripts: async f => { calls.unreg.push(f); },
  } };
}

(async () => {
  for (const build of ['sera_extension', 'sera_extension_firefox']) {
    const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, build, 'manifest.json'), 'utf8'));
    await check(build + ': manifest has no static content_scripts, keeps host_permissions', () => {
      assert.strictEqual(manifest.content_scripts, undefined);
      assert(manifest.host_permissions.includes('https://*/*'));
      assert(manifest.permissions.includes('scripting'));
    });
    await check(build + ': matches = base + service hosts, https only, subdomains too, bad hosts dropped', () => {
      const ctx = load(build, { value: [] }, scriptingApi());
      const m = ctx.scaScopeMatches(['portal.example.com', 'EVIL/*', 'a b', '']);
      assert(m.includes('https://gst.gov.in/*') && m.includes('https://*.gst.gov.in/*'));
      assert(m.includes('https://portal.example.com/*') && m.includes('https://*.portal.example.com/*'));
      assert(m.every(x => x.startsWith('https://')));
      assert(!m.some(x => x.includes('EVIL') || x.includes(' ')));
    });
    await check(build + ': registers once, re-registers on a new list, skips an unchanged list', async () => {
      const api = scriptingApi();
      const stored = { value: ['a.example.com'] };
      const ctx = load(build, stored, api);
      ctx.applyScaScope(); await new Promise(r => setTimeout(r, 10));
      ctx.applyScaScope(); await new Promise(r => setTimeout(r, 10));
      assert.strictEqual(api.calls.reg.length, 1);
      const s = api.calls.reg[0][0];
      assert.deepStrictEqual(Array.from(s.js), ['content_scripts/sca_adapters.js', 'content_scripts/login.js']);
      assert(s.allFrames === true && s.runAt === 'document_end');
      stored.value = ['a.example.com', 'b.example.com'];
      ctx.applyScaScope(); await new Promise(r => setTimeout(r, 10));
      assert.strictEqual(api.calls.reg.length, 2);
      assert(api.calls.reg[1][0].matches.includes('https://b.example.com/*'));
    });
    await check(build + ': falls back to browser.contentScripts.register', async () => {
      const regs = []; let unregs = 0;
      const browser = { contentScripts: { register: async d => { regs.push(d); return { unregister: async () => { unregs++; } }; } } };
      const stored = { value: [] };
      const ctx = load(build, stored, { browser });
      ctx.applyScaScope(); await new Promise(r => setTimeout(r, 10));
      stored.value = ['c.example.com'];
      ctx.applyScaScope(); await new Promise(r => setTimeout(r, 10));
      assert.strictEqual(regs.length, 2);
      assert.strictEqual(unregs, 1);
      assert.deepStrictEqual(Array.from(regs[0].js.map(j => j.file)), ['content_scripts/sca_adapters.js', 'content_scripts/login.js']);
    });
  }
  process.exit(failures ? 1 : 0);
})();
