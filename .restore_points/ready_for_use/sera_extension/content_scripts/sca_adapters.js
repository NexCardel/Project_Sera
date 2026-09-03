/**
 * SCA 3.0 Portal Adapters & Normalization
 */

// Normalizes a UID exactly as sca_protocol.normalize_uid does in Python
window.normalizeUid = function(rawUid) {
  if (!rawUid) return "";
  let uid = String(rawUid);
  
  // NFKC normalization if supported
  if (uid.normalize) {
    try { uid = uid.normalize("NFKC"); } catch(e) {}
  }
  
  uid = uid.trim();
  uid = uid.replace(/\s+/g, " ");
  uid = uid.replace(/[\x00-\x1F]/g, ""); // Strip control chars
  return uid.toUpperCase();
};

class PortalAdapter {
  constructor(name) { this.name = name; }
  matchesUrl(url) { return false; }
  findUidFields(doc) { return []; }
  findPasswordFields(doc) { return []; }
  isTwoStep() { return false; }
  isLoginReady(doc) { return true; }
  findContinueButton(doc) { return null; }
  
  // Fills password and dispatches events
  fillPassword(field, password) {
    if (!field) return false;
    try { field.focus(); } catch (e) {}
    field.value = password;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.dispatchEvent(new Event("change", { bubbles: true }));
    try { field.blur(); } catch(e) {}
    return true;
  }
  
  isVisible(el) {
    if (!el || el.disabled || el.type === "hidden" || el.getAttribute("tabindex") === "-1") return false;
    try {
      const style = window.getComputedStyle(el);
      return style.display !== "none" && style.visibility !== "hidden";
    } catch (_) { return true; }
  }
}

class GstAdapter extends PortalAdapter {
  constructor() { super("gst"); }
  matchesUrl(url) { return url.includes("gst.gov.in"); }
  findUidFields(doc) { return Array.from(doc.querySelectorAll("input[id*=\"username\"]")).filter(this.isVisible); }
  findPasswordFields(doc) { return Array.from(doc.querySelectorAll("input[type=\"password\"]")).filter(this.isVisible); }
}

class IncomeTaxAdapter extends PortalAdapter {
  constructor() { super("income_tax"); }
  matchesUrl(url) { return url.includes("incometax.gov.in"); }
  findUidFields(doc) { return Array.from(doc.querySelectorAll("input[id*=\"pan\"]")).filter(this.isVisible); }
  findPasswordFields(doc) { return Array.from(doc.querySelectorAll("input[type=\"password\"]")).filter(this.isVisible); }
  isTwoStep() { return true; }
  isLoginReady(doc) { return this.findPasswordFields(doc).length > 0; }
}

class TracesAdapter extends PortalAdapter {
  constructor() { super("traces"); }
  matchesUrl(url) { return url.includes("tdscpc.gov.in"); }
  findUidFields(doc) { return Array.from(doc.querySelectorAll("input[id*=\"userId\"]")).filter(this.isVisible); }
  findPasswordFields(doc) { return Array.from(doc.querySelectorAll("input[type=\"password\"]")).filter(this.isVisible); }
}

class McaAdapter extends PortalAdapter {
  constructor() { super("mca"); }
  matchesUrl(url) { return url.includes("mca.gov.in"); }
  findUidFields(doc) { return Array.from(doc.querySelectorAll("input[id*=\"userName\"]")).filter(this.isVisible); }
  findPasswordFields(doc) { return Array.from(doc.querySelectorAll("input[type=\"password\"]")).filter(this.isVisible); }
}

class GenericAdapter extends PortalAdapter {
  constructor() { super("generic"); }
  matchesUrl(url) { return true; }
  findUidFields(doc) { 
    return Array.from(doc.querySelectorAll("input[type=\"text\"], input[type=\"email\"], input:not([type])")).filter(this.isVisible); 
  }
  findPasswordFields(doc) { return Array.from(doc.querySelectorAll("input[type=\"password\"]")).filter(this.isVisible); }
}

window.SCA_ADAPTERS = [
  new GstAdapter(),
  new IncomeTaxAdapter(),
  new TracesAdapter(),
  new McaAdapter(),
  new GenericAdapter()
];

window.getAdapterForUrl = function(url) {
  return window.SCA_ADAPTERS.find(a => a.matchesUrl(url));
};

