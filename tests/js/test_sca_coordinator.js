// node tests/js/test_sca_coordinator.js  (run by tests/test_sca_v2.py). Identifiers are fictional.
"use strict";
const assert = require("assert");
const path = require("path");
const vm = require("vm");
const SCA = require(path.join(__dirname, "..", "..", "sera_extension", "sca", "sca_coordinator.js"));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function makeArm(over = {}) {
  return Object.assign({
    arm_id: "arm_1", client_id: 7, client_id_token: "7", matched_uid: "ABCPD1234E",
    candidate_uids: ["ABCPD1234E", "CLI-0007"], sca_mode: "autofill", max_uses: 1,
    expires_at: Date.now() + 60000, business_name: "Test Co", owner_name: "",
    services: [
      { service_id: 1, name: "Income Tax", url: "https://eportal.incometax.gov.in/iec/foservices/#/login",
        host: "eportal.incometax.gov.in", password_selector: "", has_password: true },
      { service_id: 2, name: "GST", url: "https://services.gst.gov.in/services/login",
        host: "services.gst.gov.in", password_selector: "", has_password: false },
    ],
  }, over);
}

function makeEnv(settings = {}) {
  const native = [], desktop = [], scripts = [];
  const env = {
    native, desktop, scripts,
    fillResult: { filled: true },
    postNative: (m) => { native.push(m); return true; },
    postDesktop: (m) => desktop.push(m),
    getSettings: async () => Object.assign({ scaEnabled: true, scaMode: "autofill",
      allowedDomains: ["incometax.gov.in", "gst.gov.in"], assistTabs: {} }, settings),
    executeScript: async (d) => { scripts.push(d); return [{ result: d.func === SCA.fillPasswordInPage ? env.fillResult : undefined }]; },
    sessionStore: null,
  };
  return env;
}

async function armed(env, arm) {
  const c = SCA.createCoordinator(env);
  await c.handleDesktopMessage({ type: "SCA_ARM_REQUEST", command_id: "cmd_1", arm: arm || makeArm() });
  return c;
}

const portalTab = (url = "https://eportal.incometax.gov.in/iec/foservices/#/login") =>
  ({ tab: { id: 5, url }, frameId: 0, url });

// ------------------------------------------------------------ pure rules
test("host matching is label-by-label", () => {
  assert.ok(SCA.hostMatches("eportal.incometax.gov.in", "eportal.incometax.gov.in"));
  assert.ok(SCA.hostMatches("eportal.incometax.gov.in", "www.incometax.gov.in") === false);
  assert.ok(SCA.hostMatches("services.gst.gov.in", "gst.gov.in".replace(/^/, "www.")) === false);
  assert.ok(SCA.hostMatches("login.services.gst.gov.in", "services.gst.gov.in"));
  assert.ok(!SCA.hostMatches("services.gst.gov.in.evil.com", "services.gst.gov.in"));
  assert.ok(!SCA.hostMatches("evilservices.gst.gov.in", "services.gst.gov.in"));
  assert.ok(!SCA.hostMatches("gov.in", "services.gst.gov.in"));
});

test("normalizeUid matches the Python rules", () => {
  assert.strictEqual(SCA.normalizeUid("  abcpd1234e \n"), "ABCPD1234E");
  assert.strictEqual(SCA.normalizeUid("user\tname"), "USER NAME");
  assert.strictEqual(SCA.normalizeUid("①"), "1");
});

// ------------------------------------------------------------ arming
test("a v1 arm that carries passwords is refused", async () => {
  const env = makeEnv();
  const bad = makeArm();
  bad.services[0].password = "secret";
  const c = await armed(env, bad);
  assert.strictEqual(c._state().arm, null);
  assert.deepStrictEqual(env.native[0], { type: "SCA_ACK", command_id: "cmd_1" });
});

test("a retried arm command is acknowledged again but applied once", async () => {
  const env = makeEnv();
  const c = await armed(env);
  await c.handleDesktopMessage({ type: "SCA_ARM_REQUEST", command_id: "cmd_1", arm: makeArm({ arm_id: "arm_other" }) });
  assert.strictEqual(env.native.filter(m => m.type === "SCA_ACK").length, 2);
  assert.strictEqual(c._state().arm.arm_id, "arm_1");
});

// ------------------------------------------------------------ the security rules
test("the armed id on the right portal asks the desktop for that one password", async () => {
  const env = makeEnv();
  const c = await armed(env);
  const r = await c.onCandidate({ candidate: "abcpd1234e" }, portalTab());
  assert.strictEqual(r, "password-requested");
  const req = env.native.find(m => m.type === "SCA_PASSWORD_REQUEST");
  assert.strictEqual(req.service_id, 1);
  assert.strictEqual(req.page_host, "eportal.incometax.gov.in");
  assert.ok(!("password" in req));
});

test("pasting the id on any other website does nothing (no first-service fallback)", async () => {
  const env = makeEnv();
  const c = await armed(env);
  for (const url of ["https://web.whatsapp.com/", "https://eportal.incometax.gov.in.evil.com/login"]) {
    const r = await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab(url));
    assert.strictEqual(r, "not-a-portal-of-this-client", url);
  }
  // GST is the client's portal but has no usable password: explained, still no request
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" },
                     portalTab("https://services.gst.gov.in/services/login")), "blocked:no_password");
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
});

test("a site outside the approved domains gets nothing even if it matches a service", async () => {
  const env = makeEnv({ allowedDomains: ["gst.gov.in"] });
  const c = await armed(env);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "not-a-portal-of-this-client");
});

test("another client's id does nothing", async () => {
  const env = makeEnv();
  const c = await armed(env);
  assert.strictEqual(await c.onCandidate({ candidate: "XYZAB9876C" }, portalTab()), "not-this-client");
});

// A woken-up worker (or a desktop app still starting) has no arm when the first paste arrives.
// The arm retry follows a moment later; the id the user already entered must still be acted on.
test("an id pasted while unarmed is handled when the arm arrives a moment later", async () => {
  const env = makeEnv();
  const c = SCA.createCoordinator(env);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "not-armed");
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
  await c.handleDesktopMessage({ type: "SCA_ARM_REQUEST", command_id: "cmd_late", arm: makeArm() });
  const req = env.native.find(m => m.type === "SCA_PASSWORD_REQUEST");
  assert.ok(req, "the late arm should act on the id the user already pasted");
  assert.strictEqual(req.service_id, 1);
});

test("a late arm for a different client does not act on the pasted id", async () => {
  const env = makeEnv();
  const c = SCA.createCoordinator(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  await c.handleDesktopMessage({ type: "SCA_ARM_REQUEST", command_id: "cmd_late_other",
    arm: makeArm({ arm_id: "arm_other", matched_uid: "XYZAB9876C", candidate_uids: ["XYZAB9876C"] }) });
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
});

test("an old pasted id is not replayed when an arm arrives much later", async () => {
  const env = makeEnv();
  let t = 1000000;
  env.now = () => t;
  const c = SCA.createCoordinator(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  t += 16000;
  await c.handleDesktopMessage({ type: "SCA_ARM_REQUEST", command_id: "cmd_stale", arm: makeArm() });
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
});

test("a replayed id shows the widget in widget mode, and repeating the paste asks only once", async () => {
  const env = makeEnv({ scaMode: "widget" });
  const c = SCA.createCoordinator(env);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "not-armed");
  await c.handleDesktopMessage({ type: "SCA_ARM_REQUEST", command_id: "cmd_late_widget",
    arm: makeArm({ arm_id: "arm_widget", sca_mode: "widget" }) });
  assert.ok(env.scripts.some(s => s.func === SCA.showWidgetInPage));
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "already-handled");
});

test("an expired arm does nothing", async () => {
  const env = makeEnv();
  const c = await armed(env, makeArm({ expires_at: Date.now() + 5 }));
  await new Promise(r => setTimeout(r, 20));
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "not-armed");
});

// ------------------------------------------------------------ grant -> fill -> report
test("a grant fills only the frame where the id was entered, then reports and consumes", async () => {
  const env = makeEnv();
  const c = await armed(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, { tab: { id: 5, url: "https://eportal.incometax.gov.in/x" }, frameId: 3,
                                                     url: "https://eportal.incometax.gov.in/x" });
  const req = env.native.find(m => m.type === "SCA_PASSWORD_REQUEST");
  await c.handleDesktopMessage({ type: "SCA_PASSWORD_GRANT", request_id: req.request_id, arm_id: "arm_1",
                                 service_id: 1, password: "pw-for-test", password_selector: "" });
  const fill = env.scripts.find(s => s.func === SCA.fillPasswordInPage);
  assert.deepStrictEqual(fill.target, { tabId: 5, frameIds: [3] });
  assert.ok(!("allFrames" in fill.target));
  const result = env.desktop.find(m => m.type === "SCA_FILL_RESULT");
  assert.strictEqual(result.result, "filled");
  assert.ok(!JSON.stringify(env.desktop).includes("pw-for-test"));
  assert.strictEqual(c._state().arm, null);              // max_uses 1 -> consumed
});

test("a failed fill can be retried; a grant for someone else's request is ignored", async () => {
  const env = makeEnv();
  env.fillResult = { filled: false, reason: "no visible password field" };
  const c = await armed(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  const req = env.native.find(m => m.type === "SCA_PASSWORD_REQUEST");
  await c.handleDesktopMessage({ type: "SCA_PASSWORD_GRANT", request_id: "req_not_ours", password: "x" });
  assert.strictEqual(env.scripts.length, 0);
  await c.handleDesktopMessage({ type: "SCA_PASSWORD_GRANT", request_id: req.request_id, arm_id: "arm_1",
                                 service_id: 1, password: "pw" });
  assert.strictEqual(env.desktop.find(m => m.type === "SCA_FILL_RESULT").result, "failed");
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "password-requested");
});

test("a desktop denial is reported as a failed fill with its reason, and can be retried", async () => {
  const env = makeEnv();
  const c = await armed(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  const req = env.native.find(m => m.type === "SCA_PASSWORD_REQUEST");
  await c.handleDesktopMessage({ type: "SCA_PASSWORD_DENIED", request_id: req.request_id, arm_id: "arm_1",
                                 reason: "the arm expired" });
  const res = env.desktop.filter(m => m.type === "SCA_FILL_RESULT");
  assert.strictEqual(res.length, 1);
  assert.deepStrictEqual([res[0].arm_id, res[0].service_id, res[0].result, res[0].reason],
                         ["arm_1", 1, "failed", "the arm expired"]);
  assert.strictEqual(env.scripts.length, 0);
  await c.handleDesktopMessage({ type: "SCA_PASSWORD_DENIED", request_id: "req_not_ours", reason: "x" });
  assert.strictEqual(env.desktop.filter(m => m.type === "SCA_FILL_RESULT").length, 1);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "password-requested");
});

test("repeated input events for one paste ask only once", async () => {
  const env = makeEnv();
  const c = await armed(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "already-handled");
  assert.strictEqual(env.native.filter(m => m.type === "SCA_PASSWORD_REQUEST").length, 1);
});

test("a second client in the same tab works (no sticky per-tab 'filled' flag)", async () => {
  const env = makeEnv();
  const c = await armed(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  const req = env.native.find(m => m.type === "SCA_PASSWORD_REQUEST");
  await c.handleDesktopMessage({ type: "SCA_PASSWORD_GRANT", request_id: req.request_id, password: "pw" });
  await c.handleDesktopMessage({ type: "SCA_ARM_REQUEST", command_id: "cmd_2",
    arm: makeArm({ arm_id: "arm_2", candidate_uids: ["XYZAB9876C"], matched_uid: "XYZAB9876C" }) });
  assert.strictEqual(await c.onCandidate({ candidate: "XYZAB9876C" }, portalTab()), "password-requested");
});

test("widget mode shows a card that holds no password", async () => {
  const env = makeEnv({ scaMode: "widget" });
  const c = await armed(env, makeArm({ sca_mode: "widget" }));
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "widget-shown");
  const w = env.scripts.find(s => s.func === SCA.showWidgetInPage);
  assert.ok(!JSON.stringify(w.args).includes("pw"));
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
  const r = await c.onWidgetFill({ arm_id: "arm_1", service_id: 1, frame_id: 0 }, portalTab());
  assert.strictEqual(r, "password-requested");
});

test("an SMTI or MECP card open in the tab keeps SCA quiet in that tab", async () => {
  for (const kind of ["smti", "mecp"]) {
    const env = makeEnv({ assistTabs: { 5: kind } });
    const c = await armed(env);
    assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "assist-open-in-tab");
    assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
  }
});

test("the assist lock is per tab: another tab still gets SCA", async () => {
  const env = makeEnv({ assistTabs: { 9: "smti" } });
  const c = await armed(env);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "password-requested");
  assert.ok(env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
});

test("an Income Tax password SCC has not verified is explained once, not silently skipped", async () => {
  const env = makeEnv();
  const arm = makeArm();
  arm.services[0].has_password = false;
  arm.services[0].blocked = "scc_unverified";
  const c = await armed(env, arm);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "blocked:scc_unverified");
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  const reports = env.desktop.filter(m => m.type === "SCA_FILL_RESULT");
  assert.strictEqual(reports.length, 1);
  assert.deepStrictEqual([reports[0].result, reports[0].reason, reports[0].service_id], ["failed", "scc_unverified", 1]);
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
});

test("an unverified Income Tax password is never auto-filled: the card asks, the click confirms", async () => {
  const env = makeEnv();
  const arm = makeArm();
  arm.services[0].needs_confirm = true;
  const c = await armed(env, arm);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "widget-shown");
  const card = env.scripts.find(s => s.func === SCA.showWidgetInPage);
  assert.ok(/not been verified by SCC/.test(card.args[6]));
  assert.ok(!env.native.some(m => m.type === "SCA_PASSWORD_REQUEST"));
  await c.onWidgetFill({ arm_id: "arm_1", service_id: 1, frame_id: 0 }, portalTab());
  const req = env.native.find(m => m.type === "SCA_PASSWORD_REQUEST");
  assert.strictEqual(req.confirmed, true);
});

test("an automatic request is never marked confirmed", async () => {
  const env = makeEnv();
  const c = await armed(env);
  await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab());
  assert.strictEqual(env.native.find(m => m.type === "SCA_PASSWORD_REQUEST").confirmed, false);
});

// ------------------------------------------------------------ the page fill (SCK runs this too)
function fakePage(opts) {
  opts = opts || {};
  const field = {
    tagName: "INPUT", type: opts.type || "password", id: opts.id || "", name: opts.name || "",
    value: "", disabled: false, readOnly: false, events: [], focused: false,
    getAttribute(k) { return k === "autocomplete" ? (opts.autocomplete || null) : null; },
    getBoundingClientRect() { return { width: 200, height: 28 }; },
    focus() { this.focused = true; },
    dispatchEvent(e) { this.events.push(e.type); return true; },
  };
  const proto = {};
  Object.defineProperty(proto, "value", { configurable: true, set(v) { field.value = v; } });
  const document = {
    querySelectorAll(sel) { return sel === "input[type='password']" && field.type === "password" ? [field] : []; },
    querySelector() { return null; },
  };
  const window = {
    HTMLInputElement: { prototype: proto }, HTMLTextAreaElement: { prototype: proto },
    getComputedStyle: () => ({ display: "block", visibility: "visible" }),
  };
  const ctx = vm.createContext({
    window, document,
    Event: class { constructor(t) { this.type = t; } },
    InputEvent: class { constructor(t, o) { this.type = t; Object.assign(this, o || {}); } },
    setTimeout, Date, Object,
  });
  const fill = vm.runInContext("(" + SCA.fillPasswordInPage.toString() + ")", ctx);
  return { field, window, fill };
}

test("SCK's fill path uses sera_dom.findField, so a revealed password box is filled", async () => {
  const page = fakePage({ type: "text", id: "loginPassword" });   // portal eye switched it to text
  let asked = 0;
  page.window.__seraDom = { findField: (doc, kind) => { asked++; assert.strictEqual(kind, "pass"); return page.field; } };

  const res = await page.fill("pw-for-test", "", "sck:1:1", 50);

  assert.strictEqual(res.filled, true);
  assert.strictEqual(asked, 1);
  assert.strictEqual(page.field.value, "pw-for-test");
  assert.deepStrictEqual(page.field.events, ["input", "change"]);
});

test("without sera_dom the SCA rules still fill a plain password input", async () => {
  const page = fakePage({ type: "password" });

  const res = await page.fill("pw-for-test", "", "sck:1:2", 50);

  assert.strictEqual(res.filled, true);
  assert.strictEqual(page.field.value, "pw-for-test");
});

test("the same attempt never fills the same page twice", async () => {
  const page = fakePage({ type: "password" });

  await page.fill("one", "", "sck:1:3", 50);
  const again = await page.fill("two", "", "sck:1:3", 50);

  assert.strictEqual(again.filled, false);
  assert.strictEqual(again.reason, "already filled on this page for this copy");
  assert.strictEqual(page.field.value, "one");
});

test("no password box is reported as a failed fill after the wait", async () => {
  const page = fakePage({ type: "text", id: "searchBox" });
  const started = Date.now();

  const res = await page.fill("pw", "", "sck:1:4", 120);

  assert.strictEqual(res.filled, false);
  assert.strictEqual(res.reason, "no visible password field");
  assert.ok(Date.now() - started >= 100, "it waited for the box instead of failing at once");
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   " + name); }
    catch (e) { failed++; console.log("FAIL " + name + "\n     " + (e && e.stack || e)); }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
