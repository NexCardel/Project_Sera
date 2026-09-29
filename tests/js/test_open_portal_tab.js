// openPortalTab (Part B2): load listener attached before navigating, one injection,
// no reload when already on the URL, reuse only a login-page tab, listeners removed.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.join(__dirname, '..', '..');
const LOGIN = 'https://portal.example.test/login?x=1#top';

function load(build) {
  const src = fs.readFileSync(path.join(ROOT, build, 'background.js'), 'utf8');
  const a = src.indexOf('// Read-only probe run inside a tab');
  const b = src.indexOf('function handleAutofillTab(');
  assert(a > 0 && b > a, build + ': helper block not found');
  return src.slice(a, b);
}

function makeEnv(build, tabs, passwordTabIds) {
  const listeners = new Set();
  const log = [];
  const timers = [];
  let nextId = 100;
  const chrome = {
    runtime: { lastError: null },
    windows: { update: (id, o, cb) => { log.push('focus'); cb && cb(); } },
    scripting: {
      executeScript: (d, cb) => {
        log.push('probe:' + d.target.tabId);
        cb([{ result: passwordTabIds.includes(d.target.tabId) }]);
      },
    },
    tabs: {
      query: (q, cb) => cb(tabs),
      create: (o, cb) => { const t = { id: nextId++, windowId: 1, url: o.url, status: 'loading' }; tabs.push(t); log.push('create'); cb(t); },
      update: (id, o, cb) => { log.push('update:' + (o.url ? 'nav' : 'activate')); cb && cb(); },
      onUpdated: {
        addListener: f => { listeners.add(f); log.push('listen'); },
        removeListener: f => { listeners.delete(f); },
      },
    },
  };
  const ctx = vm.createContext({
    chrome, URL, console,
    setTimeout: (f, ms) => { timers.push({ f, ms }); return timers.length; },
    clearTimeout: () => {},
    document: {}, getComputedStyle: () => ({}),
  });
  vm.runInContext(load(build), ctx);
  const fire = (tabId, status) => [...listeners].forEach(f => f(tabId, { status }));
  return { ctx, listeners, log, timers, fire };
}

for (const build of ['sera_extension', 'sera_extension_firefox']) {
  // new tab: opened, injected once
  {
    const e = makeEnv(build, [{ id: 1, windowId: 1, url: 'https://other.test/', status: 'complete' }], []);
    const ready = [];
    e.ctx.openPortalTab(LOGIN, id => ready.push(id));
    e.fire(100, 'complete'); e.fire(100, 'complete');
    assert.deepStrictEqual(ready, [100], build + ': new tab injects exactly once');
    assert.strictEqual(e.listeners.size, 0, build + ': listener removed after use');
  }
  // tab already on the login URL and loaded: inject at once, no navigation, no listener
  {
    const e = makeEnv(build, [{ id: 5, windowId: 1, url: LOGIN, status: 'complete' }], []);
    const ready = [];
    e.ctx.openPortalTab(LOGIN, id => ready.push(id));
    assert.deepStrictEqual(ready, [5]);
    assert(!e.log.includes('update:nav') && !e.log.includes('create'), build + ': no reload');
    assert.strictEqual(e.listeners.size, 0);
  }
  // same login path, different query: reused, listener attached before navigating, stale complete ignored
  {
    const e = makeEnv(build, [{ id: 6, windowId: 1, url: 'https://portal.example.test/login?y=2', status: 'complete' }], []);
    const ready = [];
    e.ctx.openPortalTab(LOGIN, id => ready.push(id));
    assert(e.log.indexOf('listen') < e.log.indexOf('update:nav'), build + ': listener before navigation');
    e.fire(6, 'complete');
    assert.deepStrictEqual(ready, [], build + ': complete before loading is ignored');
    e.fire(6, 'loading'); e.fire(6, 'complete'); e.fire(6, 'complete');
    assert.deepStrictEqual(ready, [6]);
    assert.strictEqual(e.listeners.size, 0);
  }
  // other page of the portal (logged in, no password box): not touched, new tab opened
  {
    const e = makeEnv(build, [{ id: 7, windowId: 1, url: 'https://portal.example.test/dashboard', status: 'complete' }], []);
    e.ctx.openPortalTab(LOGIN, () => {});
    assert(e.log.includes('probe:7') && e.log.includes('create'), build + ': probed then opened new tab');
    assert(!e.log.includes('update:nav'), build + ': other tab never navigated');
  }
  // other path but a visible password box: reused
  {
    const e = makeEnv(build, [{ id: 8, windowId: 1, url: 'https://portal.example.test/signin', status: 'complete' }], [8]);
    e.ctx.openPortalTab(LOGIN, () => {});
    assert(e.log.includes('update:nav') && !e.log.includes('create'), build + ': password-box tab reused');
  }
  // a non-portal tab whose URL only mentions the portal host: never probed, never navigated
  {
    const e = makeEnv(build, [
      { id: 9, windowId: 1, url: 'https://search.test/?q=portal.example.test', status: 'complete' },
      { id: 10, windowId: 1, url: 'https://portal.example.test.evil.test/login', status: 'complete' },
    ], [9, 10]);
    e.ctx.openPortalTab(LOGIN, () => {});
    assert(!e.log.some(l => l.startsWith('probe:')), build + ': URL-substring tabs not probed');
    assert(e.log.includes('create') && !e.log.includes('update:nav'), build + ': new tab opened instead');
  }
  // listener removed after 30 s when the page never finishes
  {
    const e = makeEnv(build, [], []);
    e.ctx.openPortalTab(LOGIN, () => assert.fail('must not inject'));
    assert.strictEqual(e.listeners.size, 1);
    const t = e.timers.find(x => x.ms === 30000);
    assert(t, build + ': 30 s cleanup timer set');
    t.f();
    assert.strictEqual(e.listeners.size, 0);
  }
}
console.log('open_portal_tab: all passed');
