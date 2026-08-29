/**
 * sdc_toast.js — Ultra-Stealth, Compact SDC Toast Notification Engine
 * ---------------------------------------------------------------------
 * Features:
 * 1. Compact Mini-Pill Design (Width: 245px, bottom-left corner).
 * 2. Rapid 1.1s auto-dismiss (snappy, non-intrusive, zero obstruction).
 * 3. Closed Shadow DOM encapsulation (100% invisible to portal scripts).
 * 4. Pure GPU transform/opacity transitions (NO blur or repaints).
 * 5. Theme Modes:
 *    - 'start':   🟢 Emerald Green (#4CF9B7)
 *    - 'capture': 🟢 Glowing Green / Gold (#39FF14 / #FFA657)
 *    - 'update':  🔷 Sapphire Blue (#388BFD) for in-place updates on page revisits
 *    - 'logout':  ⚪ Slate Gray (#8B949E)
 */

(function () {
  'use strict';

  if (window.SDCToast) return; // Prevent duplicate instantiation

  let _host = null;
  let _shadow = null;
  let _activeCard = null;
  let _dismissTimer = null;
  let _isPaused = false;
  let _toastEnabled = true;

  // Load initial toast preference from chrome storage
  try {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local) {
      chrome.storage.local.get(['sdcToastEnabled'], (res) => {
        if (res && res.sdcToastEnabled !== undefined) {
          _toastEnabled = res.sdcToastEnabled !== false;
        }
      });
      chrome.storage.onChanged.addListener((changes, area) => {
        if (area === 'local' && changes.sdcToastEnabled) {
          _toastEnabled = changes.sdcToastEnabled.newValue !== false;
          if (!_toastEnabled) SDCToast.dismiss();
        }
      });
    }
  } catch (_) {}

  function _ensureContainer() {
    if (_host && _shadow) return _shadow;

    // Attach to documentElement positioned at BOTTOM-LEFT corner (out of way of portal action buttons)
    _host = document.createElement('div');
    _host.id = 'sera-sdc-notify-root';
    _host.style.cssText = 'all: initial !important; position: fixed !important; bottom: 16px !important; left: 16px !important; z-index: 2147483647 !important; pointer-events: none !important;';

    try {
      _shadow = _host.attachShadow({ mode: 'closed' });
    } catch (_) {
      _shadow = _host.attachShadow({ mode: 'open' });
    }

    const style = document.createElement('style');
    style.textContent = `
      * {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      }
      .sera-toast-container {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        pointer-events: none;
      }
      .sera-toast-card {
        pointer-events: auto;
        width: 250px;
        max-width: calc(100vw - 32px);
        background: #161B22;
        border-radius: 6px;
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.5), 0 1px 4px rgba(0, 0, 0, 0.3);
        padding: 7px 10px;
        color: #F0F6FC;
        opacity: 0;
        transform: translateY(8px) scale(0.97);
        transition: transform 0.16s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.16s ease-out;
        will-change: transform, opacity;
        border: 1px solid #30363D;
        border-left-width: 3.5px;
        cursor: default;
        user-select: none;
      }
      .sera-toast-card.visible {
        opacity: 1;
        transform: translateY(0) scale(1);
      }
      .sera-toast-card.pulse {
        animation: sera-pulse 0.25s ease-in-out;
      }
      @keyframes sera-pulse {
        0% { transform: scale(1); }
        50% { transform: scale(1.02); }
        100% { transform: scale(1); }
      }

      /* Color Variations */
      .sera-toast-card.theme-start {
        border-left-color: #4CF9B7;
      }
      .sera-toast-card.theme-capture {
        border-left-color: #39FF14;
      }
      .sera-toast-card.theme-update {
        border-left-color: #388BFD;
        background: #0F172A;
      }
      .sera-toast-card.theme-logout {
        border-left-color: #8B949E;
      }

      .sera-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 3px;
      }
      .sera-title-group {
        display: flex;
        align-items: center;
        gap: 5px;
        overflow: hidden;
      }
      .sera-badge {
        font-size: 8.5px;
        font-weight: 700;
        text-transform: uppercase;
        padding: 1px 4px;
        border-radius: 3px;
        letter-spacing: 0.3px;
        white-space: nowrap;
      }
      .theme-start .sera-badge {
        background: rgba(76, 249, 183, 0.15);
        color: #4CF9B7;
        border: 1px solid rgba(76, 249, 183, 0.3);
      }
      .theme-capture .sera-badge {
        background: rgba(57, 255, 20, 0.15);
        color: #39FF14;
        border: 1px solid rgba(57, 255, 20, 0.3);
      }
      .theme-update .sera-badge {
        background: rgba(56, 139, 253, 0.2);
        color: #58A6FF;
        border: 1px solid rgba(56, 139, 253, 0.4);
      }
      .theme-logout .sera-badge {
        background: rgba(139, 148, 158, 0.15);
        color: #8B949E;
        border: 1px solid rgba(139, 148, 158, 0.3);
      }

      .sera-title {
        font-size: 11px;
        font-weight: 600;
        color: #FFFFFF;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .sera-close-btn {
        background: transparent;
        border: none;
        color: #8B949E;
        cursor: pointer;
        font-size: 11px;
        line-height: 1;
        padding: 1px 3px;
        border-radius: 2px;
      }
      .sera-close-btn:hover {
        color: #F0F6FC;
        background: rgba(255, 255, 255, 0.1);
      }

      .sera-body {
        font-size: 10px;
        line-height: 1.35;
        color: #8B949E;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .sera-details-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 3px;
        margin-top: 3px;
      }
      .sera-chip {
        font-size: 9.5px;
        background: #21262D;
        border: 1px solid #30363D;
        border-radius: 3px;
        padding: 1px 4px;
        color: #E6EDF3;
        white-space: nowrap;
      }
      .theme-update .sera-chip {
        background: #1E293B;
        border-color: #334155;
      }
      .sera-chip-label {
        color: #8B949E;
        margin-right: 2px;
      }
      .sera-chip-value-ack {
        font-family: Consolas, monospace;
        color: #39FF14;
        font-weight: 700;
      }
      .sera-chip-value-pan {
        font-family: Consolas, monospace;
        color: #79C0FF;
        font-weight: 600;
      }
    `;

    _shadow.appendChild(style);

    const container = document.createElement('div');
    container.className = 'sera-toast-container';
    _shadow.appendChild(container);

    (document.documentElement || document.body).appendChild(_host);
    return _shadow;
  }

  const SDCToast = {
    /**
     * Display or in-place update an SDC toast notification.
     * @param {Object} options
     * @param {'start'|'capture'|'update'|'logout'} options.type - Visual theme category
     * @param {string} options.badge - Badge text (e.g. 'SDC ACTIVE', 'CAPTURED', 'UPDATED')
     * @param {string} options.title - Header title
     * @param {string} options.message - Descriptive text
     * @param {Array<{label: string, value: string, isAck?: boolean, isPan?: boolean}>} [options.chips] - Data chips
     * @param {number} [options.duration=1100] - Duration in ms before auto-dismiss (default ~1.1s)
     */
    show(options = {}) {
      if (!_toastEnabled) return;

      const type = options.type || 'capture';
      const badge = options.badge || (type === 'update' ? 'UPDATED' : (type === 'start' ? 'SDC ACTIVE' : (type === 'logout' ? 'LOGOUT' : 'CAPTURED')));
      const title = options.title || 'Sera SDC';
      const message = options.message || '';
      const chips = options.chips || [];
      const duration = options.duration !== undefined ? options.duration : 1100;

      const shadow = _ensureContainer();
      const container = shadow.querySelector('.sera-toast-container');
      if (!container) return;

      if (_dismissTimer) {
        clearTimeout(_dismissTimer);
        _dismissTimer = null;
      }

      // If a card already exists, update in-place with a subtle pulse animation
      if (_activeCard && container.contains(_activeCard)) {
        _activeCard.className = `sera-toast-card visible theme-${type} pulse`;
        setTimeout(() => {
          if (_activeCard) _activeCard.classList.remove('pulse');
        }, 250);

        _activeCard.querySelector('.sera-badge').textContent = badge;
        _activeCard.querySelector('.sera-title').textContent = title;
        _activeCard.querySelector('.sera-body').textContent = message;

        const detailsEl = _activeCard.querySelector('.sera-details-grid');
        detailsEl.innerHTML = '';
        chips.slice(0, 3).forEach(c => {
          if (!c.value) return;
          const chipEl = document.createElement('span');
          chipEl.className = 'sera-chip';
          const valClass = c.isAck ? 'sera-chip-value-ack' : (c.isPan ? 'sera-chip-value-pan' : '');
          chipEl.innerHTML = `<span class="sera-chip-label">${c.label}:</span><span class="${valClass}">${c.value}</span>`;
          detailsEl.appendChild(chipEl);
        });

        _startDismissTimer(duration);
        return;
      }

      // Create new card
      const card = document.createElement('div');
      card.className = `sera-toast-card theme-${type}`;

      let chipsHtml = '';
      chips.slice(0, 3).forEach(c => {
        if (!c.value) return;
        const valClass = c.isAck ? 'sera-chip-value-ack' : (c.isPan ? 'sera-chip-value-pan' : '');
        chipsHtml += `<span class="sera-chip"><span class="sera-chip-label">${c.label}:</span><span class="${valClass}">${c.value}</span></span>`;
      });

      card.innerHTML = `
        <div class="sera-header">
          <div class="sera-title-group">
            <span class="sera-badge">${badge}</span>
            <span class="sera-title">${title}</span>
          </div>
          <button class="sera-close-btn" title="Dismiss">✕</button>
        </div>
        <div class="sera-body">${message}</div>
        <div class="sera-details-grid">${chipsHtml}</div>
      `;

      card.querySelector('.sera-close-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        SDCToast.dismiss();
      });

      // Hover to pause timer
      card.addEventListener('mouseenter', () => {
        _isPaused = true;
        if (_dismissTimer) clearTimeout(_dismissTimer);
      });
      card.addEventListener('mouseleave', () => {
        _isPaused = false;
        _startDismissTimer(1000);
      });

      container.innerHTML = '';
      container.appendChild(card);
      _activeCard = card;

      // Trigger enter animation on next frame
      requestAnimationFrame(() => {
        card.classList.add('visible');
      });

      _startDismissTimer(duration);
    },

    /**
     * Dismiss current toast with fast fade out.
     */
    dismiss() {
      if (_dismissTimer) {
        clearTimeout(_dismissTimer);
        _dismissTimer = null;
      }
      if (_activeCard) {
        _activeCard.classList.remove('visible');
        setTimeout(() => {
          if (_activeCard && _activeCard.parentNode) {
            _activeCard.parentNode.removeChild(_activeCard);
          }
          _activeCard = null;
        }, 160);
      }
    }
  };

  function _startDismissTimer(ms) {
    if (ms <= 0 || _isPaused) return;
    if (_dismissTimer) clearTimeout(_dismissTimer);
    _dismissTimer = setTimeout(() => {
      if (!_isPaused) SDCToast.dismiss();
    }, ms);
  }

  // Expose globally to SDC engine
  window.SDCToast = SDCToast;
})();
