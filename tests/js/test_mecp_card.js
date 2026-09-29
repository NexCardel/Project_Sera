// MECP card (Part E): closed shadow root, timer bar with pause on hover, x / timeout / both-copied
// all close it and clear the payload, clipboard clearing. Fake DOM and fake timers. Test data is fictional.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.join(__dirname, '..', '..');
const BUILDS = ['sera_extension', 'sera_extension_firefox'];
const PASSWORD = 'Test#Pass-123';
const USERID = 'ABCDE1234F';

function widgetSource(build) {
  const src = fs.readFileSync(path.join(ROOT, build, 'background.js'), 'utf8');
  const start = src.indexOf('function mecpWidget(');
  const end = src.indexOf('function handleMECPTab(');
  assert(start > 0 && end > start, build + ': mecpWidget not found');
  return src.slice(start, end);
}

function makeWorld(build) {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  const messages = [];
  const sent = [];
  const clip = { text: '' };
  const listeners = [];   // chrome.runtime.onMessage listeners (the SCC card's desktop updates)
  const state = { host: null, shadowMode: null, shadowChildren: [] };

  function fakeEl(tag) {
    const e = {
      tag, id: '', className: '', textContent: '', innerHTML: '', style: {}, children: [], listeners: {},
      classes: new Set(), isConnected: false,
      append(...c) { e.children.push(...c); },
      appendChild(c) { e.children.push(c); return c; },
      insertBefore(c) { e.children.push(c); },
      addEventListener(t, f) { (e.listeners[t] = e.listeners[t] || []).push(f); },
      setAttribute() {},
      remove() { e.isConnected = false; },
      attachShadow(init) {
        state.host = e;
        state.shadowMode = init.mode;
        return { appendChild(c) { state.shadowChildren.push(c); return c; } };
      },
    };
    e.classList = {
      add(c) { e.classes.add(c); }, remove(c) { e.classes.delete(c); }, contains(c) { return e.classes.has(c); },
      toggle(c, on) { if (on === undefined ? !e.classes.has(c) : on) e.classes.add(c); else e.classes.delete(c); },
    };
    return e;
  }

  const documentElement = { appendChild(c) { c.isConnected = true; return c; } };
  const win = {
    document: {
      getElementById: () => null,
      createElement: fakeEl,
      documentElement,
      body: fakeEl('body'),
      execCommand() { return true; },
    },
    navigator: { clipboard: {
      writeText: async (v) => { clip.text = v; },
      readText: async () => clip.text,
    } },
    chrome: { runtime: {
      sendMessage: (m) => { messages.push(m.type); sent.push(m); },
      onMessage: { addListener: (f) => listeners.push(f), removeListener: (f) => listeners.splice(listeners.indexOf(f), 1) },
    } },
    getComputedStyle: () => ({ transform: 'matrix(0.5, 0, 0, 1, 0, 0)' }),
    addEventListener() {}, removeEventListener() {},
    Date: { now: () => now },
    setTimeout: (f, ms) => { const id = ++seq; timers.set(id, { f, at: now + ms }); return id; },
    clearTimeout: (id) => { timers.delete(id); },
  };
  win.window = win;
  vm.createContext(win);
  vm.runInContext(fs.readFileSync(path.join(ROOT, build, 'content_scripts', 'sera_dom.js'), 'utf8'), win);

  async function flush() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
  async function advance(ms) {
    const target = now + ms;
    for (;;) {
      let next = null;
      for (const [id, t] of timers) if (t.at <= target && (!next || t.at < next.t.at)) next = { id, t };
      if (!next) break;
      now = next.t.at;
      timers.delete(next.id);
      next.t.f();
      await flush();
    }
    now = target;
    await flush();
  }

  function find(node, cls, out) {
    out = out || [];
    if (node && node.className === cls) out.push(node);
    for (const c of (node && node.children) || []) find(c, cls, out);
    return out;
  }
  const query = (cls) => state.shadowChildren.flatMap(c => find(c, cls));

  function open(args) {
    vm.runInContext(widgetSource(build), win);
    win.mecpWidget(...args);
  }
  const deliver = (msg) => listeners.slice().forEach(f => f(msg));
  return { win, state, messages, sent, clip, advance, flush, query, open, deliver };
}

const argsFor = (userid, clearSeconds) => [userid, PASSWORD, 'Test Client', 90000, false, [], clearSeconds, ''];
const sccArgs = (combos, clearSeconds) => [USERID, '', 'PAN: ' + USERID, 90000, true, combos, clearSeconds, 'att-1'];
const COMBOS = [
  { id: 1, label: 'Combo 1', value: 'Combo#1' },
  { id: 2, label: 'Combo 2', value: 'Combo#2' },
  { id: 3, label: 'Saved password', value: 'Saved#3' },
];

(async () => {
  for (const build of BUILDS) {
    const label = build + ': ';
    const open = (w, userid, clearSeconds) => w.open(argsFor(userid, clearSeconds));

    // closed shadow root, card is up
    {
      const w = makeWorld(build);
      open(w, USERID, 30);
      assert.strictEqual(w.state.shadowMode, 'closed', label + 'shadow root must be closed');
      assert(w.state.host.isConnected, label + 'card is on screen');
      assert(w.query('timer-bar').length === 1, label + 'timer bar present');
    }

    // timeout closes and clears the payload (MECP_CLOSED, not the SCC-ending message)
    {
      const w = makeWorld(build);
      open(w, USERID, 30);
      await w.advance(89000);
      assert(w.state.host.isConnected, label + 'still up before 90 s');
      await w.advance(1500);
      assert(!w.state.host.isConnected, label + 'closed at 90 s');
      assert.deepStrictEqual(w.messages, ['MECP_CLOSED'], label + 'timeout tells the background to clear');
    }

    // x closes and clears
    {
      const w = makeWorld(build);
      open(w, USERID, 30);
      w.query('close-btn')[0].onclick();
      assert(!w.state.host.isConnected, label + 'x closes');
      assert.deepStrictEqual(w.messages, ['MECP_DISMISSED'], label + 'x tells the background to clear');
    }

    // hovering pauses the timer, leaving resumes it with the time that was left
    {
      const w = makeWorld(build);
      open(w, USERID, 30);
      const box = w.query('box')[0];
      await w.advance(30000);
      box.listeners.mouseenter.forEach(f => f());
      await w.advance(200000);
      assert(w.state.host.isConnected, label + 'paused card stays');
      box.listeners.mouseleave.forEach(f => f());
      await w.advance(59000);
      assert(w.state.host.isConnected, label + 'resumed card has ~60 s left');
      await w.advance(2000);
      assert(!w.state.host.isConnected, label + 'closes when the remaining time is used');
    }

    // D6: closes only after both User ID and password are copied
    {
      const w = makeWorld(build);
      open(w, USERID, 30);
      const [uidCopy, passCopy] = w.query('copy-btn');
      passCopy.onclick();
      await w.advance(5000);
      assert(w.state.host.isConnected, label + 'password alone does not close the card');
      uidCopy.onclick();
      await w.advance(1300);
      assert(!w.state.host.isConnected, label + 'both copied closes the card');
      assert.deepStrictEqual(w.messages, ['MECP_CLOSED'], label + 'both copied clears the payload');
    }

    // a hover while both-copied is closing must not bring the full countdown back
    {
      const w = makeWorld(build);
      open(w, USERID, 30);
      const box = w.query('box')[0];
      const [uidCopy, passCopy] = w.query('copy-btn');
      box.listeners.mouseenter.forEach(f => f());
      uidCopy.onclick();
      passCopy.onclick();
      box.listeners.mouseleave.forEach(f => f());
      await w.advance(1300);
      assert(!w.state.host.isConnected, label + 'leaving after the last copy still closes');
    }

    // clipboard cleared after the desktop's seconds, only while it still holds the password
    {
      const w = makeWorld(build);
      open(w, USERID, 20);
      w.query('copy-btn')[1].onclick();
      await w.flush();
      assert.strictEqual(w.clip.text, PASSWORD, label + 'password copied');
      await w.advance(19000);
      assert.strictEqual(w.clip.text, PASSWORD, label + 'not cleared early');
      await w.advance(1500);
      assert.strictEqual(w.clip.text, '', label + 'cleared after clipboard_clear_seconds');
    }
    {
      const w = makeWorld(build);
      open(w, USERID, 20);
      w.query('copy-btn')[1].onclick();
      await w.flush();
      w.clip.text = 'something the staff copied since';
      await w.advance(21000);
      assert.strictEqual(w.clip.text, 'something the staff copied since', label + 'other clipboard text is left alone');
    }
    {
      const w = makeWorld(build);
      open(w, USERID, 20);
      w.query('copy-btn')[1].onclick();
      w.win.navigator.clipboard.readText = async () => { throw new Error('not focused'); };
      await w.advance(21000);
      assert.strictEqual(w.clip.text, PASSWORD, label + 'a refused read is swallowed (best effort)');
    }

    // no User ID row -> the password copy alone closes the card
    {
      const w = makeWorld(build);
      open(w, '', 30);
      w.query('copy-btn')[0].onclick();
      await w.advance(1300);
      assert(!w.state.host.isConnected, label + 'no User ID: password copy closes the card');
    }

    // SCC card (desktop-fed): renders exactly the combinations it is given, closed root, a
    // 'This one worked' button per row, no timeout, no SCC_PASSWORD_COPIED, no storage
    {
      const s = makeWorld(build);
      s.open(sccArgs(COMBOS, 30));
      assert.strictEqual(s.state.shadowMode, 'closed', label + 'SCC card keeps the closed shadow root');
      const values = s.query('field-value').map(e => e.textContent);
      assert.deepStrictEqual(values, [USERID, 'Combo#1', 'Combo#2', 'Saved#3'], label + 'PAN then the given rows only');
      assert.strictEqual(s.query('worked-btn').length, COMBOS.length, label + 'one worked button per row');
      assert.strictEqual(s.query('timer-container').length, 0, label + 'SCC card has no countdown');

      const copies = s.query('copy-btn'); // PAN copy + one per row
      assert.strictEqual(copies.length, COMBOS.length + 1);
      copies.forEach(b => b.onclick());
      await s.advance(200000);
      assert(s.state.host.isConnected, label + 'copying everything or waiting does not close the SCC card');
      assert.deepStrictEqual(s.messages, [], label + 'copies send nothing (no SCC_PASSWORD_COPIED)');

      s.query('worked-btn')[1].onclick();
      assert.deepStrictEqual(s.messages, ['scc_row_worked'], label + 'a click sends scc_row_worked');
      assert.deepStrictEqual(JSON.parse(JSON.stringify(s.sent[0])), { type: 'scc_row_worked', attempt_id: 'att-1', row_label: 'Combo 2' },
        label + 'the message names the attempt and the row label only');
      assert(s.state.host.isConnected, label + 'the card stays until the desktop closes it');

      s.query('close-btn')[0].onclick();
      assert(!s.state.host.isConnected, label + 'x closes the SCC card');
      assert.deepStrictEqual(JSON.parse(JSON.stringify(s.sent[1])), { type: 'MECP_DISMISSED', attempt_id: 'att-1' }, label + 'x names the attempt');
    }

    // SCC copy puts the row's text on the clipboard and clears it later
    {
      const s = makeWorld(build);
      s.open(sccArgs(COMBOS, 20));
      s.query('copy-btn')[2].onclick();
      await s.flush();
      assert.strictEqual(s.clip.text, 'Combo#2', label + 'row copied');
      await s.advance(21000);
      assert.strictEqual(s.clip.text, '', label + 'row cleared from the clipboard');
    }

    // Step 5: the desktop asks which row worked; the None button shows only then; a ✗ row can't be credited
    {
      const s = makeWorld(build);
      s.open(sccArgs(COMBOS, 30));
      const none = s.query('none-btn')[0];
      assert(none, label + 'the card has a None button');
      assert.notStrictEqual(none.style.display, 'block', label + 'hidden until the desktop asks');
      s.deliver({ type: 'SERA_SCC_CARD_UPDATE', attempt_id: 'att-1', failed: ['Combo 1'], next: 'Combo 2', message: 'm', stop: false, ask: null });
      assert.notStrictEqual(none.style.display, 'block', label + 'a step-4 update does not show it');
      const worked = s.query('worked-btn');
      assert(worked[0].disabled, label + 'a ✗ row\'s worked button is disabled');
      s.deliver({ type: 'SERA_SCC_CARD_UPDATE', attempt_id: 'att-1', failed: ['Combo 1'], next: null, message: 'which?', stop: false, ask: ['Combo 2', 'Saved password'] });
      assert.strictEqual(none.style.display, 'block', label + 'asking shows None');
      const rows = s.query('field-row').filter(r => r.classes.has('ask'));
      assert.strictEqual(rows.length, 2, label + 'the asked-about rows are highlighted');
      s.deliver({ type: 'SERA_SCC_CARD_UPDATE', attempt_id: 'other', failed: [], next: null, message: '', stop: false, ask: null });
      assert.strictEqual(none.style.display, 'block', label + 'another attempt\'s update is ignored');
      none.onclick();
      assert.deepStrictEqual(JSON.parse(JSON.stringify(s.sent[s.sent.length - 1])), { type: 'scc_row_none', attempt_id: 'att-1' },
        label + 'None names the attempt only');
      assert(none.disabled, label + 'None is sent once');
    }

    // SCC mode with no rows is the plain card
    {
      const s = makeWorld(build);
      s.open(sccArgs([], 30));
      assert.strictEqual(s.query('worked-btn').length, 0, label + 'no rows: no SCC card');
    }
  }
  console.log('MECP card tests passed');
})().catch(e => { console.error(e); process.exit(1); });
