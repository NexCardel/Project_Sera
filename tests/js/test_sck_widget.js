// SCK card: the in-browser universal card with Fill & Test and direct save.
//
// The card is the only SCK widget (the desktop Test & Fill bar is gone). Fill & Test goes through
// the background (SCK_FILL_REQUEST -> SCA's fillPasswordInPage), and the background hands the
// candidate to the desktop in memory so a success that arrives after the page navigates can still
// be saved. When the desktop did not take the candidate (held: false) the card saves it itself.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.join(__dirname, '..', '..');
const BUILDS = ['sera_extension', 'sera_extension_firefox'];
const SERVICE_ID = 42;
const CLIENT_ID = 101;
const CLIENT_NAME = 'A. Paruli Dresses';
const PORTAL = 'income tax';
const TEST_PWD = 'NewSecret#2026';

function extractSckWidget(build) {
  const src = fs.readFileSync(path.join(ROOT, build, 'background.js'), 'utf8');
  const start = src.indexOf('function sckWidget(');
  const end = src.indexOf('\nfunction mecpWidget(', start);
  assert(start > 0 && end > start, build + ': sckWidget not found');
  return src.slice(start, end);
}

function makeWorld(build, opts) {
  opts = opts || {};
  let now = 0;
  let seq = 0;
  const timers = new Map();
  const intervals = new Map();
  const messages = [];
  const sent = [];
  const listeners = [];
  const state = { host: null, shadowMode: null, shadowChildren: [] };
  // What the background answers to SCK_FILL_REQUEST. held: true means the desktop took the
  // candidate and will save it on "worked"; the card must not save it a second time.
  const fillReply = opts.fillReply || { ok: true, held: false };

  function fakeEl(tag) {
    const e = {
      tag, id: '', className: '', textContent: '', innerHTML: '', style: {}, children: [], listeners: {},
      classList: {
        _classes: new Set(),
        add(c) { this._classes.add(c); },
        remove(c) { this._classes.delete(c); },
        contains(c) { return this._classes.has(c); }
      },
      isConnected: false,
      get value() { return this._val !== undefined ? this._val : ''; },
      set value(v) { this._val = v; },
      type: tag === 'input' ? 'text' : '',
      disabled: false,
      append(...c) { e.children.push(...c); },
      appendChild(c) { e.children.push(c); return c; },
      insertBefore(c) { e.children.push(c); },
      addEventListener(t, f) { (e.listeners[t] = e.listeners[t] || []).push(f); },
      dispatchEvent(evt) {
        const list = e.listeners[evt.type] || [];
        list.forEach(f => f(evt));
      },
      setAttribute(k, v) { e[k] = v; },
      getAttribute(k) { return e[k]; },
      getBoundingClientRect() { return { left: 100, top: 100, right: 300, bottom: 140, width: 200, height: 40 }; },
      focus() {},
      remove() { e.isConnected = false; },
      matches() { return false; },
      attachShadow(init) {
        state.host = e;
        state.shadowMode = init.mode;
        return {
          append(...c) { state.shadowChildren.push(...c); },
          appendChild(c) { state.shadowChildren.push(c); return c; },
          activeElement: null
        };
      },
    };
    return e;
  }

  const pagePasswordField = fakeEl('input');
  pagePasswordField.type = 'password';
  pagePasswordField.id = 'portal-password';
  pagePasswordField.isConnected = true;

  // The SCC card, when the test wants it on screen, is what getPlacement() looks for.
  const sccEl = opts.sccOnScreen ? fakeEl('div') : null;

  const win = {
    document: {
      getElementById: (id) => (id === 'sera-sck-host' ? null : null),
      createElement: fakeEl,
      documentElement: { clientWidth: 1280, clientHeight: 800 },
      body: { appendChild(c) { c.isConnected = true; return c; } },
      querySelectorAll: (sel) => {
        if (sel === "input[type='password']") return [pagePasswordField];
        return [];
      },
      querySelector: (sel) => (
        opts.sccOnScreen && sel === '#sera-mecp-host[data-scc="1"]' ? sccEl : null
      ),
    },
    location: { href: 'https://eportal.incometax.gov.in/iec/foservices/#/login' },
    chrome: {
      runtime: {
        sendMessage: (m, cb) => {
          messages.push(m.type);
          sent.push(m);
          if (typeof cb === 'function') cb(m.type === 'SCK_FILL_REQUEST' ? fillReply : { ok: true });
        },
        onMessage: {
          addListener: (f) => listeners.push(f),
          removeListener: (f) => {
            const idx = listeners.indexOf(f);
            if (idx >= 0) listeners.splice(idx, 1);
          }
        }
      }
    },
    getComputedStyle: () => ({ display: 'block', visibility: 'visible', position: 'static' }),
    addEventListener() {}, removeEventListener() {},
    requestAnimationFrame: (f) => f(),
    Date: { now: () => now },
    setTimeout: (f, ms) => { const id = ++seq; timers.set(id, { f, at: now + ms }); return id; },
    clearTimeout: (id) => { timers.delete(id); },
    setInterval: (f, ms) => { const id = ++seq; intervals.set(id, { f, ms, next: now + ms }); return id; },
    clearInterval: (id) => { intervals.delete(id); },
    InputEvent: function(t, init) { this.type = t; this.inputType = init && init.inputType; },
    Event: function(t) { this.type = t; },
    HTMLInputElement: { prototype: { get value() { return this._val; }, set value(v) { this._val = v; } } },
    HTMLTextAreaElement: { prototype: { get value() { return this._val; }, set value(v) { this._val = v; } } }
  };
  win.window = win;
  win.self = win;
  win.top = win;
  vm.createContext(win);

  function find(node, cls, out) {
    out = out || [];
    if (node && (node.className || '').split(' ').includes(cls)) out.push(node);
    for (const c of (node && node.children) || []) find(c, cls, out);
    return out;
  }
  const query = (cls) => state.shadowChildren.flatMap(c => find(c, cls));

  function open(dashboardLink) {
    vm.runInContext(extractSckWidget(build), win);
    win.sckWidget(SERVICE_ID, CLIENT_ID, CLIENT_NAME, PORTAL,
      'https://eportal.incometax.gov.in/iec/foservices/#/login',
      dashboardLink || 'https://eportal.incometax.gov.in/iec/foservices/#/dashboard');
  }

  const deliver = (msg) => listeners.slice().forEach(f => f(msg));

  function type(value) {
    const pwdInput = query('pwd')[0];
    pwdInput.value = value;
    pwdInput.listeners.input.forEach(f => f());
    return pwdInput;
  }

  return { win, state, messages, sent, query, open, deliver, type, pagePasswordField };
}

(async () => {
  for (const build of BUILDS) {
    const label = build + ': ';

    // 1. Renders properly with closed shadow root, title, input, and both buttons
    {
      const w = makeWorld(build);
      w.open();
      assert.strictEqual(w.state.shadowMode, 'closed', label + 'shadow root closed');
      assert(w.state.host.isConnected, label + 'card is connected to DOM');

      const fills = w.query('btn-fill');
      const saves = w.query('btn-save');
      assert.strictEqual(fills.length, 1, label + 'Fill & Test button present');
      assert.strictEqual(saves.length, 1, label + 'Save Directly button present');
      assert(fills[0].disabled, label + 'Fill button disabled initially');
      assert(saves[0].disabled, label + 'Save button disabled initially');
    }

    // 2. Typing enables both Fill & Test and Save Directly
    {
      const w = makeWorld(build);
      w.open();
      w.type(TEST_PWD);
      assert(!w.query('btn-fill')[0].disabled, label + 'Fill button enabled with input');
      assert(!w.query('btn-save')[0].disabled, label + 'Save button enabled with input');
    }

    // 3. Fill & Test asks the background to fill the portal's password field
    {
      const w = makeWorld(build);
      w.open();
      w.type(TEST_PWD);
      w.query('btn-fill')[0].onclick();

      const asks = w.sent.filter(m => m.type === 'SCK_FILL_REQUEST');
      assert.strictEqual(asks.length, 1, label + 'fill request sent to the background');
      assert.strictEqual(asks[0].service_id, SERVICE_ID, label + 'fill request carries the service');
      assert.strictEqual(asks[0].client_id, CLIENT_ID, label + 'fill request carries the client');
      assert.strictEqual(asks[0].password, TEST_PWD, label + 'fill request carries the password');
      const statusEl = w.query('status')[0];
      assert(statusEl.textContent.includes('Password filled'), label + 'status confirms fill');
    }

    // 4. A fill the page could not take says so and re-enables the buttons
    {
      const w = makeWorld(build, { fillReply: { ok: false, reason: 'no_field' } });
      w.open();
      w.type(TEST_PWD);
      const fillBtn = w.query('btn-fill')[0];
      fillBtn.onclick();

      const statusEl = w.query('status')[0];
      assert(statusEl.textContent.includes('Could not find the password box'),
        label + 'no-field failure surfaced');
      assert(!fillBtn.disabled, label + 'fill re-enabled after a failed fill');
      assert(!w.query('btn-save')[0].disabled, label + 'save re-enabled after a failed fill');
    }

    // 5. Portal rejection outcome shakes and warns
    {
      const w = makeWorld(build);
      w.open();
      w.type(TEST_PWD);
      const fillBtn = w.query('btn-fill')[0];
      fillBtn.onclick();

      w.deliver({ type: 'SERA_SCK_OUTCOME', service_id: SERVICE_ID, kind: 'wrong_password' });
      const statusEl = w.query('status')[0];
      assert(statusEl.textContent.includes('Portal rejected'), label + 'wrong password outcome surfaced');
      assert(!fillBtn.disabled, label + 'fill re-enabled after rejection');
    }

    // 6. Dashboard success saves the candidate when the desktop is not holding it
    {
      const w = makeWorld(build, { fillReply: { ok: true, held: false } });
      w.open();
      w.type(TEST_PWD);
      w.query('btn-fill')[0].onclick();

      w.deliver({ type: 'SERA_SCK_OUTCOME', service_id: SERVICE_ID, kind: 'worked' });
      const saveMsgs = w.sent.filter(m => m.type === 'SCK_SAVE_PASSWORD');
      assert.strictEqual(saveMsgs.length, 1, label + 'candidate auto-saved on success');
      assert.strictEqual(saveMsgs[0].password, TEST_PWD, label + 'saved password matches candidate');
    }

    // 7. Dashboard success does NOT save twice when the desktop holds the candidate
    {
      const w = makeWorld(build, { fillReply: { ok: true, held: true } });
      w.open();
      w.type(TEST_PWD);
      w.query('btn-fill')[0].onclick();

      w.deliver({ type: 'SERA_SCK_OUTCOME', service_id: SERVICE_ID, kind: 'worked' });
      const saveMsgs = w.sent.filter(m => m.type === 'SCK_SAVE_PASSWORD');
      assert.strictEqual(saveMsgs.length, 0, label + 'desktop-held candidate is not saved twice');
      const statusEl = w.query('status')[0];
      assert(statusEl.textContent.includes('Login succeeded'), label + 'success surfaced on the card');
    }

    // 8. Save Directly sends password immediately
    {
      const w = makeWorld(build);
      w.open();
      w.type('DirectPassword#99');
      w.query('btn-save')[0].onclick();

      const saveMsgs = w.sent.filter(m => m.type === 'SCK_SAVE_PASSWORD');
      assert.strictEqual(saveMsgs.length, 1, label + 'direct save sent');
      assert.strictEqual(saveMsgs[0].password, 'DirectPassword#99', label + 'direct save password matches');
    }

    // 9. A save result marks the card saved, and a failed save re-enables it
    {
      const w = makeWorld(build);
      w.open();
      w.type(TEST_PWD);
      w.query('btn-save')[0].onclick();

      w.deliver({ type: 'SERA_SCK_RESULT', service_id: SERVICE_ID, ok: true,
                  message: 'New password saved' });
      assert(w.query('btn-save')[0].textContent === 'Saved', label + 'card shows Saved');
      assert(w.query('btn-save')[0].disabled, label + 'buttons disabled after a save');
    }
    {
      const w = makeWorld(build);
      w.open();
      w.type(TEST_PWD);
      w.query('btn-save')[0].onclick();

      w.deliver({ type: 'SERA_SCK_RESULT', service_id: SERVICE_ID, ok: false,
                  message: 'The password could not be saved.' });
      assert(!w.query('btn-save')[0].disabled, label + 'save re-enabled after a failed save');
      assert(w.query('status')[0].textContent.includes('could not be saved'),
        label + 'failed save surfaced');
    }

    // 10. A locked account stops the card
    {
      const w = makeWorld(build);
      w.open();
      w.type(TEST_PWD);
      w.deliver({ type: 'SERA_SCK_OUTCOME', service_id: SERVICE_ID, kind: 'locked' });
      assert(w.query('btn-fill')[0].disabled, label + 'fill disabled when locked');
      assert(w.query('status')[0].textContent.includes('Account locked'), label + 'locked surfaced');
    }

    // 11. Close button dismisses
    {
      const w = makeWorld(build);
      w.open();
      w.query('close-btn')[0].onclick();
      assert(w.messages.includes('SCK_DISMISSED'), label + 'dismiss sent');
    }

    // 12. With the SCC card on screen the card moves to the top-left corner
    {
      const w = makeWorld(build);
      w.open();
      const card = w.query('card')[0];
      assert.strictEqual(card.style.bottom, '24px', label + 'default placement is bottom-right');
      assert.strictEqual(card.style.right, '24px', label + 'default placement is bottom-right');
    }
    {
      const w = makeWorld(build, { sccOnScreen: true });
      w.open();
      const card = w.query('card')[0];
      assert.strictEqual(card.style.top, '24px', label + 'SCC on screen moves the card to the top');
      assert.strictEqual(card.style.left, '24px', label + 'SCC on screen moves the card to the left');
    }
  }

  console.log('SCK card tests passed');
})().catch(e => { console.error(e); process.exit(1); });
