// node tests/js/test_sca_coordinator.js  (run by tests/test_sca_v2.py). Identifiers are fictional.
"use strict";
const assert = require("assert");
const path = require("path");
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
      allowedDomains: ["incometax.gov.in", "gst.gov.in"], manualAssistActive: false }, settings),
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

test("manual assist running on the tab blocks SCA", async () => {
  const env = makeEnv({ manualAssistActive: true });
  const c = await armed(env);
  assert.strictEqual(await c.onCandidate({ candidate: "ABCPD1234E" }, portalTab()), "manual-assist-active");
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

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   " + name); }
    catch (e) { failed++; console.log("FAIL " + name + "\n     " + (e && e.stack || e)); }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`);
  process.exit(failed ? 1 : 0);
})();
