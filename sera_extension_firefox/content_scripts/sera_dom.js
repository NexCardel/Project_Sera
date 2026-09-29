// Shared DOM rules for the scripts Sera injects into a portal page (Fast Autofill, SMTI).
// Injected on demand with chrome.scripting.executeScript({ files }) just before the function
// that uses it; it is not a manifest content script and never runs by itself.
(function () {
  if (window.__seraDom) return;

  // The one visibility rule: zero size, aria-hidden, tabindex=-1, type=hidden and the
  // portal's name=hiddenPassword decoy are all "not visible".
  function isVisible(el) {
    if (!el) return false;
    if (el.name === 'hiddenPassword' || el.getAttribute('tabindex') === '-1' || el.getAttribute('aria-hidden') === 'true' || el.closest('[aria-hidden="true"]')) return false;
    if (el.type === 'hidden') return false;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') === 0) return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    } catch (e) {
      return true;
    }
  }

  function isPasswordInput(el) {
    return !!el && el.tagName === 'INPUT' && String(el.type || '').toLowerCase() === 'password';
  }

  function cleanSelector(sel) {
    return (sel || '').trim().replace(/\s+\[/g, '[').replace(/input\s+/g, 'input');
  }

  function matchesSelector(el, selectorStr) {
    for (const p of String(selectorStr || '').split(',').map(s => s.trim()).filter(Boolean)) {
      try { if (el.matches(p)) return true; } catch (e) {}
    }
    return false;
  }

  function queryVisible(doc, selectorStr, accept) {
    const results = [];
    for (const p of String(selectorStr || '').split(',').map(s => s.trim()).filter(Boolean)) {
      try {
        for (const el of doc.querySelectorAll(p)) {
          if (isVisible(el) && accept(el) && !results.includes(el)) results.push(el);
        }
      } catch (e) {}
    }
    return results;
  }

  const TEXT_TYPES = ['', 'text', 'email', 'tel', 'number', 'search', 'url'];

  // 'user': a visible non-password text input that is not the configured password field.
  // 'pass': a visible password input, or a visible match of the configured password selector.
  // null when nothing fits, so the caller copies instead of typing into the wrong box.
  function findField(doc, kind, selector, fallbacks, otherSelector) {
    const isUser = kind === 'user';
    const accept = isUser
      ? (el) => el.tagName === 'INPUT' && !isPasswordInput(el) && TEXT_TYPES.includes(String(el.type || '').toLowerCase()) && !(otherSelector && matchesSelector(el, otherSelector))
      : (el) => el.tagName === 'INPUT';
    const acceptFallback = isUser ? accept : isPasswordInput;

    if (selector) {
      const hit = queryVisible(doc, cleanSelector(selector), accept)[0];
      if (hit) return hit;
    }
    for (const sel of (Array.isArray(fallbacks) ? fallbacks : [])) {
      const hit = queryVisible(doc, sel, acceptFallback)[0];
      if (hit) return hit;
    }
    if (!isUser) {
      const hit = queryVisible(doc, 'input[type="password"]', isPasswordInput)[0];
      if (hit) return hit;
    }
    try {
      const active = doc.activeElement;
      if (active && isVisible(active) && acceptFallback(active)) return active;
    } catch (e) {}
    return null;
  }

  // A visible password box, or a visible match of the configured username field.
  function hasLoginForm(doc, usernameSelector) {
    if (queryVisible(doc, 'input[type="password"]', isPasswordInput).length > 0) return true;
    return !!usernameSelector && queryVisible(doc, cleanSelector(usernameSelector), (el) => el.tagName === 'INPUT' && !isPasswordInput(el)).length > 0;
  }

  window.__seraDom = { isVisible, isPasswordInput, findField, hasLoginForm };
})();
