/*
 * Sera Clipboard Assist (SCA) - extension coordinator, protocol v2 (2026-09-22)
 * ============================================================================
 * ONE file for Chrome and Firefox: sera_extension/sca/ is copied to sera_extension_firefox/sca/
 * (tests/test_sca_v2.py fails if the two copies differ).
 *
 * Flow (see sca_protocol.py):
 *   desktop  --SCA_ARM_REQUEST (no passwords)-->  here: keep the arm in memory/session storage
 *   page     --SCA_MATCH_CANDIDATE {candidate}-->  here: is it the armed client's id, and is
 *            this frame's site one of the armed portals (exact host / sub-domain, and an
 *            approved domain)? Nothing is ever filled on any other site.
 *   here     --SCA_PASSWORD_REQUEST-->  desktop (through the native host only)
 *   desktop  --SCA_PASSWORD_GRANT / SCA_PASSWORD_DENIED-->  here
 *   here     fills the password field of THAT frame only, reports SCA_FILL_RESULT.
 *
 * Passwords exist here only between a grant and the fill; they are never stored.
 */
(function (root) {
  "use strict";

  const STORE_KEY = "scaArmV2";
  const REQUEST_TIMEOUT_MS = 15000;
  const FIELD_WAIT_MS = 90000;   // Income Tax: PAN page, Continue, then the password page
  const UID_SHAPE = /^(?:[A-Z]{5}[0-9]{4}[A-Z]|[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]|[A-Z0-9][A-Z0-9._@:/-]{2,79})$/;

  // ---------------------------------------------------------------- pure helpers (tested)
  function normalizeUid(raw) {
    if (raw === null || raw === undefined) return "";
    let uid = String(raw);
    if (uid.normalize) {
      try { uid = uid.normalize("NFKC"); } catch (_) {}
    }
    uid = uid.trim().replace(/\s+/g, " ").replace(/[\x00-\x1F]/g, "");
    return uid.toUpperCase();
  }

  function looksLikeUid(value) {
    return UID_SHAPE.test(normalizeUid(value));
  }

  function hostOf(url) {
    try { return new URL(url).hostname.toLowerCase().replace(/\.$/, ""); } catch (_) { return ""; }
  }

  // Same rule as sca_protocol.host_matches: equal, or one a sub-domain of the other, compared
  // label by label (so "services.gst.gov.in.evil.com" is NOT "services.gst.gov.in"); both need
  // three labels or more.
  function hostMatches(pageHost, serviceHost) {
    const page = String(pageHost || "").toLowerCase().replace(/\.$/, "");
    const svc = String(serviceHost || "").toLowerCase().replace(/\.$/, "");
    if (!page || !svc || page.split(".").length < 3 || svc.split(".").length < 3) return false;
    return page === svc || page.endsWith("." + svc) || svc.endsWith("." + page);
  }

  function isApprovedDomain(host, approved) {
    if (!Array.isArray(approved) || approved.length === 0) return true;   // desktop list not synced yet
    const h = String(host || "").toLowerCase();
    return approved.some(d => {
      const dom = String(d || "").toLowerCase().replace(/^\.+|\.+$/g, "");
      return dom && (h === dom || h.endsWith("." + dom));
    });
  }

  // Which armed portal may this frame's page receive a password for? null = none.
  function pickService(arm, frameHost, approvedDomains) {
    if (!arm || !Array.isArray(arm.services)) return null;
    if (!isApprovedDomain(frameHost, approvedDomains)) return null;
    return arm.services.find(s => s && s.has_password && hostMatches(frameHost, s.host || hostOf(s.url))) || null;
  }

  function isArmLive(arm, now) {
    return !!(arm && arm.arm_id && typeof arm.expires_at === "number" && arm.expires_at > (now || Date.now()));
  }

  function candidateMatches(arm, candidate) {
    const c = normalizeUid(candidate);
    if (!c || !arm || !Array.isArray(arm.candidate_uids)) return false;
    return arm.candidate_uids.some(u => normalizeUid(u) === c);
  }

  // ---------------------------------------------------------------- functions run in the page
  // Runs in the login page's frame (extension's isolated world). Waits for a visible password
  // box, fills it, returns {filled, reason}. Only a real password box (or the portal's
  // configured selector) is ever filled.
  async function fillPasswordInPage(pwd, configuredSelector, armId, waitMs) {
    if (window.__seraScaFilledArm === armId) {
      return { filled: false, reason: "already filled on this page for this copy" };
    }
    const visible = el => {
      if (!el || el.disabled || el.readOnly) return false;
      try {
        const st = window.getComputedStyle(el);
        if (st.display === "none" || st.visibility === "hidden") return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      } catch (_) { return true; }
    };
    const find = () => {
      if (configuredSelector) {
        try {
          for (const el of document.querySelectorAll(configuredSelector)) {
            if (el.tagName === "INPUT" && visible(el)) return el;
          }
        } catch (_) {}
      }
      for (const el of document.querySelectorAll("input[type='password']")) {
        if (visible(el)) return el;
      }
      return null;
    };
    const deadline = Date.now() + (waitMs || 30000);
    let field = find();
    while (!field && Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 150));
      field = find();
    }
    if (!field) return { filled: false, reason: "no visible password field" };
    try { field.focus(); } catch (_) {}
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(field, pwd);
    } catch (_) { field.value = pwd; }
    field.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
    window.__seraScaFilledArm = armId;
    return { filled: true };
  }

  // The page card: an "Inject Password" button. It holds NO password - the click asks the
  // extension, which then asks the desktop. Used in widget mode, and always for an Income Tax
  // password SCC has not verified (`note` says so; only this click can release it). When the
  // password box is in this frame the card waits for it (Income Tax: PAN page -> Continue ->
  // password page) instead of appearing on the PAN page and timing out.
  function showWidgetInPage(armId, serviceId, fieldFrameId, bizName, ownName, portalName, note) {
    if (window.self !== window.top) return;
    const hostId = "sera-sca-widget-host";
    if (document.getElementById(hostId) || window.__seraScaCardFor === armId) return;
    window.__seraScaCardFor = armId;
    const hasField = () => Array.from(document.querySelectorAll("input[type='password']")).some(el => {
      try {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && window.getComputedStyle(el).visibility !== "hidden";
      } catch (_) { return false; }
    });
    const render = () => {
      const host = document.createElement("div");
      host.id = hostId;
      const shadow = host.attachShadow({ mode: "closed" });
      const style = document.createElement("style");
      style.textContent = `
        .card { position: fixed; top: 20px; right: 24px; z-index: 2147483647; width: 320px;
          padding: 14px 16px; background: linear-gradient(145deg, #111814, #0B130E);
          border: 1.5px solid #2E9B5F; border-radius: 12px; color: #FFFFFF; box-sizing: border-box;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          transform: translateX(120%); opacity: 0;
          transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.35s ease; }
        .card.warn { border-color: #D29922; }
        .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
        .badge { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
          color: #4CF9B7; background: rgba(46, 155, 95, 0.22); border: 1px solid rgba(76, 249, 183, 0.35);
          padding: 3px 7px; border-radius: 6px; }
        .close-btn { background: transparent; border: none; cursor: pointer; font-size: 14px;
          color: #889988; line-height: 1; padding: 2px 4px; border-radius: 4px; }
        .close-btn:hover { color: #FFFFFF; }
        .title { font-size: 14px; font-weight: 700; line-height: 1.3; white-space: nowrap;
          overflow: hidden; text-overflow: ellipsis; margin-bottom: 2px; }
        .subtitle { font-size: 12px; color: #9FB3A8; line-height: 1.2; margin-bottom: 10px;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .note { font-size: 12px; color: #E3B341; line-height: 1.35; margin-bottom: 10px; }
        .btn-inject { display: flex; align-items: center; justify-content: center; width: 100%;
          padding: 9px 12px; font-size: 13px; font-weight: 700; color: #FFFFFF; background: #2E9B5F;
          border: 1px solid #34B76D; border-radius: 8px; cursor: pointer; box-sizing: border-box; }
        .btn-inject:hover { background: #34B76D; }
        .timer-container { margin-top: 10px; height: 3px; background: rgba(255, 255, 255, 0.08);
          border-radius: 2px; overflow: hidden; }
        .timer-bar { height: 100%; width: 100%; background: #2E9B5F; transform-origin: left;
          transition: transform 60s linear; }`;
      const card = document.createElement("div");
      card.className = note ? "card warn" : "card";
      const header = document.createElement("div");
      header.className = "header";
      const badge = document.createElement("div");
      badge.className = "badge";
      badge.textContent = "SCA";
      const closeBtn = document.createElement("button");
      closeBtn.className = "close-btn";
      closeBtn.textContent = "✕";
      header.append(badge, closeBtn);
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = bizName || "Client";
      const subtitle = document.createElement("div");
      subtitle.className = "subtitle";
      subtitle.textContent = ownName ? `${ownName} • ${portalName}` : String(portalName || "");
      const parts = [header, title, subtitle];
      if (note) {
        const n = document.createElement("div");
        n.className = "note";
        n.textContent = note;
        parts.push(n);
      }
      const btn = document.createElement("button");
      btn.className = "btn-inject";
      btn.textContent = "Inject Password";
      const timer = document.createElement("div");
      timer.className = "timer-container";
      const bar = document.createElement("div");
      bar.className = "timer-bar";
      timer.appendChild(bar);
      card.append(...parts, btn, timer);
      shadow.append(style, card);
      document.body.appendChild(host);
      setTimeout(() => { card.style.transform = "translateX(0)"; card.style.opacity = "1"; bar.style.transform = "scaleX(0)"; }, 40);
      const dismiss = () => {
        card.style.transform = "translateX(120%)";
        card.style.opacity = "0";
        setTimeout(() => { if (host.isConnected) host.remove(); }, 380);
      };
      const autoTimer = setTimeout(dismiss, 60000);
      closeBtn.onclick = () => { clearTimeout(autoTimer); dismiss(); };
      btn.onclick = () => {
        clearTimeout(autoTimer);
        try {
          chrome.runtime.sendMessage({ type: "SCA_WIDGET_FILL", arm_id: armId, service_id: serviceId, frame_id: fieldFrameId });
        } catch (_) {}
        window.__seraScaCardFor = null;      // a failed fill can be retried by entering the id again
        dismiss();
      };
    };
    if (fieldFrameId !== 0 || hasField()) { render(); return; }
    const waitUntil = Date.now() + 180000;
    const poll = setInterval(() => {
      if (hasField()) { clearInterval(poll); render(); }
      else if (Date.now() > waitUntil) { clearInterval(poll); window.__seraScaCardFor = null; }
    }, 250);
  }

  // ---------------------------------------------------------------- coordinator
  function createCoordinator(env) {
    // env: { postNative(msg) -> bool, postDesktop(msg), getSettings() -> Promise<{scaEnabled,
    //        scaMode, allowedDomains, manualAssistActive}>, executeScript(details) -> Promise,
    //        sessionStore (chrome.storage.session or null), now() }
    const now = env.now || (() => Date.now());
    let arm = null;
    let loaded = false;
    const pending = new Map();      // request_id -> {tabId, frameId, service, key, timer}
    const busy = new Set();         // arm:tab:frame:service with a request/fill under way or done
    const seenCommands = [];

    function remember(cmdId) {
      if (seenCommands.includes(cmdId)) return false;
      seenCommands.push(cmdId);
      if (seenCommands.length > 200) seenCommands.shift();
      return true;
    }

    async function loadArm() {
      if (!loaded) {
        loaded = true;
        if (env.sessionStore) {
          try {
            const data = await env.sessionStore.get(STORE_KEY);
            arm = (data && data[STORE_KEY]) || arm;
          } catch (_) {}
        }
      }
      if (arm && !isArmLive(arm, now())) await clearArm("EXPIRED");
      return arm;
    }

    async function saveArm() {
      if (!env.sessionStore) return;
      try {
        if (arm) await env.sessionStore.set({ [STORE_KEY]: arm });
        else await env.sessionStore.remove(STORE_KEY);
      } catch (_) {}
    }

    function report(msg) {
      try { env.postDesktop(msg); } catch (_) {}
    }

    async function clearArm(state) {
      const old = arm;
      arm = null;
      busy.clear();
      await saveArm();
      if (old) report({ type: "SCA_STATE", arm_id: old.arm_id, state: state || "IDLE" });
    }

    // Desktop -> extension. Returns true when the message was an SCA one.
    async function handleDesktopMessage(msg) {
      if (!msg || typeof msg.type !== "string" || !msg.type.startsWith("SCA_")) return false;
      if (msg.command_id) {
        env.postNative({ type: "SCA_ACK", command_id: msg.command_id });
        if (!remember(msg.command_id)) return true;           // a retry of one already handled
      }
      switch (msg.type) {
        case "SCA_ARM_REQUEST": {
          const settings = await env.getSettings();
          if (settings.scaEnabled === false) return true;
          const next = msg.arm;
          if (!next || !isArmLive(next, now())) return true;
          if ((next.services || []).some(s => s && "password" in s)) return true;  // v1 arm: refuse
          await loadArm();
          arm = next;
          busy.clear();
          await saveArm();
          report({ type: "SCA_STATE", arm_id: arm.arm_id, state: "ARMED" });
          return true;
        }
        case "SCA_DISARM_REQUEST":
          await clearArm("IDLE");
          return true;
        case "SCA_STATE_REQUEST": {
          const a = await loadArm();
          report({ type: "SCA_STATE", arm_id: a ? a.arm_id : null, state: a ? "ARMED" : "IDLE" });
          return true;
        }
        case "SCA_PASSWORD_GRANT":
          await onGrant(msg);
          return true;
        case "SCA_PASSWORD_DENIED": {
          const p = pending.get(msg.request_id);
          if (p) {
            clearTimeout(p.timer);
            pending.delete(msg.request_id);
            busy.delete(p.key);
          }
          return true;
        }
        default:
          return true;
      }
    }

    function requestPassword(a, service, tabId, frameId, pageHost, matchedUid, key, confirmed) {
      const requestId = "req_" + Math.random().toString(16).slice(2) + now().toString(16);
      const timer = setTimeout(() => {
        if (pending.delete(requestId)) {
          busy.delete(key);
          report({ type: "SCA_FILL_RESULT", arm_id: a.arm_id, service_id: service.service_id,
                   result: "failed", reason: "no answer from the desktop app" });
        }
      }, REQUEST_TIMEOUT_MS);
      pending.set(requestId, { tabId, frameId, service, key, armId: a.arm_id, timer });
      const sent = env.postNative({
        type: "SCA_PASSWORD_REQUEST", request_id: requestId, arm_id: a.arm_id,
        service_id: service.service_id, page_host: pageHost, matched_uid: matchedUid,
        confirmed: confirmed === true,   // only from a click on the page card
      });
      if (!sent) {
        clearTimeout(timer);
        pending.delete(requestId);
        busy.delete(key);
      }
      return sent;
    }

    async function onGrant(msg) {
      const p = pending.get(msg.request_id);
      if (!p) return;                              // not ours (another browser) or timed out
      clearTimeout(p.timer);
      pending.delete(msg.request_id);
      let result = { filled: false, reason: "could not reach the page" };
      try {
        const out = await env.executeScript({
          target: { tabId: p.tabId, frameIds: [p.frameId] },
          func: fillPasswordInPage,
          args: [msg.password, msg.password_selector || p.service.password_selector || "", p.armId, FIELD_WAIT_MS],
        });
        if (out && out[0] && out[0].result) result = out[0].result;
      } catch (e) {
        result = { filled: false, reason: "the page closed or navigated away" };
      }
      if (!result.filled) busy.delete(p.key);      // let the user try again
      report({ type: "SCA_FILL_RESULT", arm_id: p.armId, service_id: p.service.service_id,
               result: result.filled ? "filled" : "failed", reason: result.reason || "" });
      if (result.filled && arm && arm.arm_id === p.armId) {
        arm.fills = (arm.fills || 0) + 1;
        if (arm.fills >= (arm.max_uses || 1)) await clearArm("CONSUMED");
        else await saveArm();
      }
    }

    // Page -> extension: something shaped like an id was entered. Returns a short status (tests).
    async function onCandidate(req, sender) {
      if (!sender || !sender.tab || typeof sender.tab.id !== "number") return "no-tab";
      const settings = await env.getSettings();
      if (settings.scaEnabled === false) return "disabled";
      if (settings.manualAssistActive) return "manual-assist-active";
      const a = await loadArm();
      if (!a) return "not-armed";
      if (!candidateMatches(a, req.candidate)) return "not-this-client";
      const frameHost = hostOf(sender.url || sender.tab.url);
      const frameId = typeof sender.frameId === "number" ? sender.frameId : 0;
      const service = pickService(a, frameHost, settings.allowedDomains);
      if (!service) {
        // This IS one of the client's portals, but the desktop has no password it may give
        // (e.g. an Income Tax password SCC has not verified): say so once, don't stay silent.
        const blocked = isApprovedDomain(frameHost, settings.allowedDomains) && (a.services || []).find(
          s => s && !s.has_password && hostMatches(frameHost, s.host || hostOf(s.url)));
        if (!blocked) return "not-a-portal-of-this-client";
        const bkey = `${a.arm_id}:${sender.tab.id}:${frameId}:${blocked.service_id}:blocked`;
        if (!busy.has(bkey)) {
          busy.add(bkey);
          report({ type: "SCA_FILL_RESULT", arm_id: a.arm_id, service_id: blocked.service_id,
                   result: "failed", reason: blocked.blocked || "no_password" });
        }
        return "blocked:" + (blocked.blocked || "no_password");
      }
      const key = `${a.arm_id}:${sender.tab.id}:${frameId}:${service.service_id}`;
      if (busy.has(key)) return "already-handled";
      busy.add(key);
      const mode = a.sca_mode || settings.scaMode || "autofill";
      // Unverified Income Tax password: never filled automatically - the card asks first.
      if (mode === "widget" || mode === "assist" || service.needs_confirm) {
        try {
          await env.executeScript({
            target: { tabId: sender.tab.id, frameIds: [0] },
            func: showWidgetInPage,
            args: [a.arm_id, service.service_id, frameId, a.business_name || "", a.owner_name || "",
                   service.name || "Portal",
                   service.needs_confirm ? "This password has not been verified by SCC yet. Inject it only if you expect it to work." : ""],
          });
        } catch (_) { busy.delete(key); return "widget-failed"; }
        return "widget-shown";
      }
      return requestPassword(a, service, sender.tab.id, frameId, frameHost, normalizeUid(req.candidate), key)
        ? "password-requested" : "desktop-unreachable";
    }

    async function onWidgetFill(req, sender) {
      if (!sender || !sender.tab) return "no-tab";
      const a = await loadArm();
      if (!a || a.arm_id !== req.arm_id) return "not-armed";
      const service = (a.services || []).find(s => s.service_id === req.service_id);
      if (!service) return "no-service";
      const frameId = typeof req.frame_id === "number" ? req.frame_id : 0;
      const key = `${a.arm_id}:${sender.tab.id}:${frameId}:${service.service_id}`;
      const settings = await env.getSettings();
      const frameHost = hostOf(sender.tab.url);
      if (!pickService(a, frameHost, settings.allowedDomains)) return "not-a-portal-of-this-client";
      return requestPassword(a, service, sender.tab.id, frameId, frameHost, a.matched_uid, key, true)
        ? "password-requested" : "desktop-unreachable";
    }

    // chrome.runtime.onMessage -> true when handled.
    function handleRuntimeMessage(req, sender) {
      if (!req || typeof req.type !== "string") return false;
      if (req.type === "SCA_MATCH_CANDIDATE") { onCandidate(req, sender); return true; }
      if (req.type === "SCA_WIDGET_FILL") { onWidgetFill(req, sender); return true; }
      if (req.type === "sca_fill_completed") { clearArm("CONSUMED"); return true; }  // an SCC/MECP fill
      return false;
    }

    return {
      handleDesktopMessage, handleRuntimeMessage, onCandidate, onWidgetFill,
      disarm: (why) => clearArm(why || "IDLE"),
      _state: () => ({ arm, pending, busy }),
    };
  }

  const api = {
    normalizeUid, looksLikeUid, hostOf, hostMatches, isApprovedDomain, pickService, isArmLive,
    candidateMatches, fillPasswordInPage, showWidgetInPage, createCoordinator,
  };
  root.SeraSCA = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof self !== "undefined" ? self : globalThis);
