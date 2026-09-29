// SMTI field picking, shared visibility rule and re-inject rule (Part D) with a tiny fake DOM.
// Test data is fictional.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.join(__dirname, '..', '..');
const BUILDS = ['sera_extension', 'sera_extension_firefox'];

// ---- fake DOM -------------------------------------------------------------------------------
function el(attrs, opts) {
  opts = opts || {};
  const e = {
    tagName: 'INPUT', type: 'text', name: '', id: '', parent: null,
    attrs: Object.assign({}, attrs),
    style: { display: 'block', visibility: 'visible', opacity: '1' },
    rect: { width: 200, height: 30 },
    getAttribute(k) { return k in this.attrs ? String(this.attrs[k]) : null; },
    getBoundingClientRect() { return this.rect; },
    closest(sel) {
      const m = /^\[([\w-]+)="([^"]*)"\]$/.exec(sel);
      for (let n = this; n; n = n.parent) if (m && n.getAttribute(m[1]) === m[2]) return n;
      return null;
    },
    matches(sel) { return matchesSimple(this, sel); },
  };
  if (attrs.type) e.type = attrs.type;
  if (attrs.name) e.name = attrs.name;
  if (attrs.id) e.id = attrs.id;
  Object.assign(e, opts);
  return e;
}

// supports: input, #id, [attr], [attr='v'], [attr*='v'], [attr$='v'] (any number, no spaces)
function matchesSimple(e, sel) {
  let rest = sel.trim();
  if (rest.startsWith('input')) { if (e.tagName !== 'INPUT') return false; rest = rest.slice(5); }
  while (rest.length) {
    let m;
    if ((m = /^#([\w-]+)/.exec(rest))) { if (e.id !== m[1]) return false; }
    else if ((m = /^\[([\w-]+)(?:([*$]?=)(?:'([^']*)'|"([^"]*)"))?\]/.exec(rest))) {
      const v = m[1] === 'type' ? e.type : m[1] === 'name' ? e.name : m[1] === 'id' ? e.id : e.getAttribute(m[1]);
      if (v === null || v === undefined) return false;
      const want = m[3] !== undefined ? m[3] : m[4];
      if (m[2] === '=' && v !== want) return false;
      if (m[2] === '*=' && !String(v).includes(want)) return false;
      if (m[2] === '$=' && !String(v).endsWith(want)) return false;
    } else throw new Error('fake DOM cannot parse selector: ' + sel);
    rest = rest.slice(m[0].length);
  }
  return true;
}

function makeDom(elements, active) {
  const doc = {
    activeElement: active || null,
    querySelectorAll(sel) { return elements.filter(e => matchesSimple(e, sel)); },
  };
  const win = { getComputedStyle: e => e.style };
  win.window = win;
  vm.createContext(win);
  return { doc, win };
}

function loadDom(build, win) {
  vm.runInContext(fs.readFileSync(path.join(ROOT, build, 'content_scripts', 'sera_dom.js'), 'utf8'), win);
  return win.__seraDom;
}

// ---- shared file is identical in both builds ------------------------------------------------
assert.strictEqual(
  fs.readFileSync(path.join(ROOT, BUILDS[0], 'content_scripts', 'sera_dom.js'), 'utf8'),
  fs.readFileSync(path.join(ROOT, BUILDS[1], 'content_scripts', 'sera_dom.js'), 'utf8'),
  'sera_dom.js differs between builds');

const USER_FB = ["input[id*='userId']", "#panAdhaarUserId", "input[type='email']", "input[name='username']", "input[id*='pan']"];
const PASS_FB = ["input[id*='psw']", "input[type='password']", "input[id*='password']", "#password"];

for (const build of BUILDS) {
  const label = build + ': ';

  // 1. visibility rules
  {
    const { win } = makeDom([]);
    const dom = loadDom(build, win);
    const v = dom.isVisible;
    assert(v(el({ type: 'text' })), label + 'plain input is visible');
    assert(!v(el({ type: 'text' }, { rect: { width: 0, height: 20 } })), label + 'zero width');
    assert(!v(el({ type: 'text' }, { rect: { width: 20, height: 0 } })), label + 'zero height');
    assert(!v(el({ type: 'text', 'aria-hidden': 'true' })), label + 'aria-hidden');
    assert(!v(el({ type: 'text', tabindex: '-1' })), label + 'tabindex -1');
    assert(!v(el({ type: 'password', name: 'hiddenPassword' })), label + 'hiddenPassword decoy');
    assert(!v(el({ type: 'hidden' })), label + 'type hidden');
    const inHidden = el({ type: 'text' });
    inHidden.parent = el({ 'aria-hidden': 'true' }, { tagName: 'DIV' });
    assert(!v(inHidden), label + 'aria-hidden ancestor');
    const none = el({ type: 'text' });
    none.style.display = 'none';
    assert(!v(none), label + 'display none');
    const clear = el({ type: 'text' });
    clear.style.opacity = '0';
    assert(!v(clear), label + 'opacity 0');
  }

  // 2. ITR password step: only the password box is on screen -> Username finds nothing
  {
    const pass = el({ type: 'password', id: 'loginPassword' });
    const userGone = el({ type: 'text', id: 'panAdhaarUserId' }, { rect: { width: 0, height: 0 } });
    const { doc, win } = makeDom([userGone, pass], pass); // password box even has focus
    const dom = loadDom(build, win);
    assert.strictEqual(dom.findField(doc, 'user', '#panAdhaarUserId', USER_FB, '#loginPassword'), null,
      label + 'Username must not pick the password box');
    assert.strictEqual(dom.findField(doc, 'pass', '#loginPassword', PASS_FB), pass, label + 'Password picks the password box');
  }

  // 3. both boxes on screen, plus the hiddenPassword decoy placed first
  {
    const decoy = el({ type: 'password', name: 'hiddenPassword' });
    const user = el({ type: 'text', id: 'panAdhaarUserId' });
    const pass = el({ type: 'password', id: 'loginPassword' });
    const { doc, win } = makeDom([decoy, user, pass]);
    const dom = loadDom(build, win);
    assert.strictEqual(dom.findField(doc, 'user', '#panAdhaarUserId', USER_FB, '#loginPassword'), user, label + 'user field');
    assert.strictEqual(dom.findField(doc, 'pass', null, PASS_FB), pass, label + 'decoy skipped for password');
    assert.strictEqual(dom.findField(doc, 'pass', "input[name='hiddenPassword']", PASS_FB), pass, label + 'configured selector pointing at the decoy falls through');
  }

  // 4. "show password" switched the box to type=text: still not a Username target, still the Password target
  {
    const shown = el({ type: 'text', id: 'loginPassword' });
    const { doc, win } = makeDom([shown], shown);
    const dom = loadDom(build, win);
    assert.strictEqual(dom.findField(doc, 'user', null, USER_FB, '#loginPassword'), null, label + 'revealed password box is not a username');
    assert.strictEqual(dom.findField(doc, 'pass', '#loginPassword', PASS_FB), shown, label + 'configured selector still fills it');
  }

  // 5. Username never falls back to a focused password box, Password never to a focused text box
  {
    const user = el({ type: 'text', id: 'somethingElse' });
    const pass = el({ type: 'password', id: 'p1' });
    const { doc, win } = makeDom([user, pass], user);
    const dom = loadDom(build, win);
    assert.strictEqual(dom.findField(doc, 'user', null, [], null), user, label + 'focused text box is a username');
    assert.strictEqual(dom.findField(doc, 'pass', null, [], null), pass, label + 'password type is found without fallbacks');
    const onlyText = makeDom([user], user);
    const dom2 = loadDom(build, onlyText.win);
    assert.strictEqual(dom2.findField(onlyText.doc, 'pass', null, [], null), null, label + 'no password box -> copy');
  }

  // 6. login-form probe
  {
    const pass = el({ type: 'password', id: 'p' });
    const user = el({ type: 'text', id: 'panAdhaarUserId' });
    const decoy = el({ type: 'password', name: 'hiddenPassword' });
    let d = makeDom([pass]); let dom = loadDom(build, d.win);
    assert.strictEqual(dom.hasLoginForm(d.doc, null), true, label + 'password box = login form');
    d = makeDom([user]); dom = loadDom(build, d.win);
    assert.strictEqual(dom.hasLoginForm(d.doc, '#panAdhaarUserId'), true, label + 'configured username = login form');
    assert.strictEqual(dom.hasLoginForm(d.doc, null), false, label + 'unconfigured lone text box is not a login form');
    d = makeDom([decoy]); dom = loadDom(build, d.win);
    assert.strictEqual(dom.hasLoginForm(d.doc, '#panAdhaarUserId'), false, label + 'decoy only = no login form');
    d = makeDom([]); dom = loadDom(build, d.win);
    assert.strictEqual(dom.hasLoginForm(d.doc, '#panAdhaarUserId'), false, label + 'empty page (logged in) = no login form');
  }

  // 7. source wiring
  const src = fs.readFileSync(path.join(ROOT, build, 'background.js'), 'utf8');
  assert(!src.includes('sca_fill_completed'), label + 'SMTI must not send sca_fill_completed');
  assert(!/scaCoordinator\.disarm\(/.test(src), label + 'SMTI must not disarm SCA');
  assert(src.includes("files: [SERA_DOM_FILE]"), label + 'shared file is injected');
  assert(!/function isVisible\(/.test(src), label + 'no second copy of isVisible in background.js');
  assert(!/function visible\(/.test(src), label + 'no second copy of visible() in the widget');
}

// ---- re-inject rule (Chrome build only; Firefox has no tabs.onUpdated re-inject) -------------
{
  const src = fs.readFileSync(path.join(ROOT, 'sera_extension', 'background.js'), 'utf8');
  const a = src.indexOf('const SERA_DOM_FILE');
  const b = src.indexOf('function handleManualAssistTab(');
  assert(a > 0 && b > a, 'reinject block not found');
  const block = src.slice(a, b);
  assert(!/\/auth|\/login|\/dashboard/.test(block), 'no URL keywords in the re-inject rule');
  const listener = src.slice(src.indexOf('chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab)'), src.indexOf('// ── SCC Webpage Link'));
  assert(!/\/auth|\/dashboard|isExplicitLogin/.test(listener), 'onUpdated has no URL keywords');

  function run(store, probeResult) {
    const injected = [];
    const probes = [];
    const ctx = vm.createContext({
      window: { __seraDom: { hasLoginForm: () => probeResult } }, document: {},
      setTimeout: f => f(),
      chrome: { scripting: { executeScript: (d) => {
        if (d.func) { probes.push(d.args); return Promise.resolve([{ result: probeResult }]); }
        return Promise.resolve([]);
      } } },
      _passwordStore: { get: (keys, cb) => cb(store), set: () => {}, remove: () => {} },
      injectManualAssist: (tabId) => injected.push(tabId),
    });
    vm.runInContext(block, ctx);
    return { reinject: (id) => vm.runInContext('maybeReinjectManualAssist(' + id + ')', ctx), injected, probes };
  }
  const tick = () => new Promise(r => setImmediate(r));
  const live = { p: 'x', url: 'https://portal.example.test/login', username_selector: '#u', expiresAt: Date.now() + 60000 };

  (async () => {
    let r = run({ manualAssistPayload: live, smtiTabIds: [7] }, true);
    r.reinject(7); await tick();
    assert.deepStrictEqual(r.injected, [7], 'opened tab + live payload + login form -> re-inject');
    assert.strictEqual(JSON.stringify(r.probes[0]), '["#u"]', 'probe gets the configured username selector');

    r = run({ manualAssistPayload: live, smtiTabIds: [7] }, true);
    r.reinject(8); await tick();
    assert.deepStrictEqual(r.injected, [], 'a tab SMTI did not open is never touched');
    assert.strictEqual(r.probes.length, 0, 'and is not even probed');

    r = run({ manualAssistPayload: live }, true);
    r.reinject(7); await tick();
    assert.deepStrictEqual(r.injected, [], 'no tab set -> nothing');

    r = run({ manualAssistPayload: Object.assign({}, live, { expiresAt: Date.now() - 1 }), smtiTabIds: [7] }, true);
    r.reinject(7); await tick();
    assert.strictEqual(r.probes.length, 0, 'expired payload -> not even probed');
    assert.deepStrictEqual(r.injected, [], 'expired payload -> nothing');

    r = run({ smtiTabIds: [7] }, true);
    r.reinject(7); await tick();
    assert.deepStrictEqual(r.injected, [], 'no payload -> nothing');

    r = run({ manualAssistPayload: live, smtiTabIds: [7] }, false);
    r.reinject(7); await tick();
    assert.deepStrictEqual(r.injected, [], 'no visible login form (logged in) -> nothing');

    console.log('test_smti_fields: all passed');
  })().catch(e => { console.error(e); process.exit(1); });
}
