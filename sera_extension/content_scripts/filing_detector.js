// filing_detector.js - Listens for SAD API captures, displays compact in-browser FST toasts, and routes filing results to app
(function() {
    console.log("Project Sera: Filing detector active with FST toast notifier.");

    // --- Compact Left-Side FST In-Browser Toast Notification Engine ---
    const SeraToastManager = (function() {
        let container = null;
        const MAX_VISIBLE = 4;
        let activeToasts = [];

        function ensureContainer() {
            if (!container || !document.documentElement.contains(container)) {
                container = document.createElement('div');
                container.id = 'sera-fst-toast-container';
                Object.assign(container.style, {
                    position: 'fixed',
                    top: '16px',
                    left: '16px',
                    right: 'auto',
                    zIndex: '2147483647',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                    pointerEvents: 'none',
                    maxWidth: '255px',
                    width: 'calc(100vw - 32px)',
                    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
                });
                document.documentElement.appendChild(container);
            }
            return container;
        }

        function createToast(detail) {
            try {
                const root = ensureContainer();

                // If at max visible, remove the oldest one smoothly
                if (activeToasts.length >= MAX_VISIBLE) {
                    const oldest = activeToasts.shift();
                    if (oldest && oldest.element) dismissToast(oldest.element);
                }

                const toast = document.createElement('div');
                Object.assign(toast.style, {
                    position: 'relative',
                    background: '#0D1117',
                    color: '#F0F6FC',
                    border: '1px solid #30363D',
                    borderLeft: '3px solid #2ED573',
                    borderRadius: '6px',
                    boxShadow: '0 8px 24px rgba(0, 0, 0, 0.65)',
                    padding: '6px 10px 8px 10px',
                    fontSize: '11px',
                    lineHeight: '1.35',
                    pointerEvents: 'auto',
                    transform: 'translateX(-115%)',
                    opacity: '0',
                    transition: 'transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.2s ease, max-height 0.2s ease',
                    boxSizing: 'border-box',
                    overflow: 'hidden'
                });

                const filingType = detail.filing_type || "Filing Record";
                const arn = (detail.arn || "").trim();
                const pan = (detail.pan || "").trim().toUpperCase();
                const clientName = (detail.client_name || detail.name || detail.taxpayer_name || "").trim();
                const period = (detail.period_label || "").trim();
                const method = detail.capture_method === "DOM_Tracker" ? "DOM" : "SAD API";

                toast.innerHTML = `
                    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:3px;">
                        <div style="display:flex; align-items:center; gap:4px; overflow:hidden;">
                            <span style="display:inline-block; width:5px; height:5px; min-width:5px; background:#2ED573; border-radius:50%; box-shadow:0 0 5px #2ED573;"></span>
                            <span style="font-weight:700; font-size:10px; color:#4CF9B7; letter-spacing:0.3px; white-space:nowrap;">⚡ FST</span>
                            <span style="background:rgba(46,213,115,0.15); color:#2ED573; border:1px solid rgba(46,213,115,0.3); border-radius:2.5px; padding:0 3px; font-size:9px; font-weight:600; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">${escapeHtml(filingType)}</span>
                        </div>
                        <button type="button" class="sera-close-btn" style="background:none; border:none; color:#8B949E; cursor:pointer; font-size:11px; line-height:1; padding:0 0 0 4px; margin:0; transition:color 0.15s;">✕</button>
                    </div>
                    <div style="display:flex; flex-direction:column; gap:1.5px; font-size:10.5px;">
                        ${clientName ? `
                        <div style="color:#FFFFFF; font-weight:600; font-size:10px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:235px;" title="${escapeHtml(clientName)}">
                            <span style="color:#8B949E; font-weight:400; font-size:9px;">Name: </span>${escapeHtml(clientName)}
                        </div>` : ''}
                        ${arn && arn !== "N/A" ? `
                        <div style="display:flex; justify-content:space-between; align-items:baseline;">
                            <span style="color:#8B949E; font-size:9.5px;">ACK:</span>
                            <span style="font-family:Consolas, Monaco, monospace; font-weight:700; color:#39FF14; font-size:10.5px; letter-spacing:0.2px;">${escapeHtml(arn)}</span>
                        </div>` : ''}
                        <div style="display:flex; justify-content:space-between; align-items:baseline;">
                            ${pan ? `<span style="color:#C9D1D9; font-weight:600;"><span style="color:#8B949E; font-weight:400; font-size:9.5px;">PAN: </span>${escapeHtml(pan)}</span>` : '<span style="color:#8B949E; font-size:9.5px;">Status: <strong style="color:#2ED573;">Captured</strong></span>'}
                            ${period ? `<span style="background:rgba(255,255,255,0.08); color:#E6EDF3; border-radius:2.5px; padding:0 3px; font-size:9px; font-weight:600;">${escapeHtml(period)}</span>` : `<span style="color:#8B949E; font-size:9px;">${method}</span>`}
                        </div>
                    </div>
                    <div style="position:absolute; bottom:0; left:0; height:1.5px; width:100%; background:rgba(255,255,255,0.08);">
                        <div class="sera-progress" style="height:100%; width:100%; background:#2ED573; transition:width 4.5s linear;"></div>
                    </div>
                `;

                const closeBtn = toast.querySelector('.sera-close-btn');
                if (closeBtn) {
                    closeBtn.onmouseenter = () => closeBtn.style.color = '#FFFFFF';
                    closeBtn.onmouseleave = () => closeBtn.style.color = '#8B949E';
                    closeBtn.onclick = (e) => {
                        e.stopPropagation();
                        dismissToast(toast);
                    };
                }

                root.appendChild(toast);
                const record = { element: toast, timer: null };
                activeToasts.push(record);

                requestAnimationFrame(() => {
                    toast.style.transform = 'translateX(0)';
                    toast.style.opacity = '1';
                    const prog = toast.querySelector('.sera-progress');
                    if (prog) {
                        requestAnimationFrame(() => {
                            prog.style.width = '0%';
                        });
                    }
                });

                record.timer = setTimeout(() => {
                    dismissToast(toast);
                }, 4600);
            } catch (err) {
                console.warn("Sera Toast Error:", err);
            }
        }

        function dismissToast(toast) {
            if (!toast || !toast.parentNode) return;
            toast.style.transform = 'translateX(-115%)';
            toast.style.opacity = '0';
            toast.style.maxHeight = '0px';
            toast.style.marginBottom = '-6px';
            toast.style.paddingTop = '0px';
            toast.style.paddingBottom = '0px';

            setTimeout(() => {
                if (toast && toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
                activeToasts = activeToasts.filter(t => t.element !== toast);
            }, 230);
        }

        function escapeHtml(str) {
            return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        return {
            notify: createToast
        };
    })();

    // Export globally for tracker.js
    window.__SERA_TOAST_NOTIFIER__ = SeraToastManager;

    window.addEventListener('SeraFSTApiCapture', (event) => {
        if (!event || !event.detail) return;
        const detail = event.detail;

        // If this capture was already sent directly by SDC, skip re-sending from filing_detector
        if (detail.capture_method && detail.capture_method.startsWith('SDC_')) {
            return;
        }

        console.log("Sera Filing Detector: Received SAD API Capture event", detail);

        if (!chrome.runtime || !chrome.runtime.id) {
            console.log("Sera Filing Detector: Extension context reloaded.");
            return;
        }

        try {
            chrome.storage.local.get(['sadBrowserNotifEnabled', 'sad_browser_notif_enabled', 'activeAutofillPayload', 'manualAssistPayload', 'mecpPayload', 'sadEnabled', 'trackerEnabled', 'allowedDomains'], (data) => {
                if (data && (data.sadEnabled === false || data.trackerEnabled === false)) {
                    return; // SAD is disabled
                }

                // Check if in-browser toast notification is enabled
                let showToast = true;
                if (data) {
                    if (data.sadBrowserNotifEnabled === false || data.sad_browser_notif_enabled === false) {
                        showToast = false;
                    } else if (data.activeAutofillPayload && data.activeAutofillPayload.sad_browser_notif_enabled === false) {
                        showToast = false;
                    }
                }
                if (showToast) {
                    SeraToastManager.notify(detail);
                }

                if (chrome.runtime.lastError || !chrome.runtime || !chrome.runtime.id) return;
                
                // Verify host is in allowed service domains if configured
                const currentHost = window.location.hostname.toLowerCase();
                const allowed = data && data.allowedDomains;
                if (Array.isArray(allowed) && allowed.length > 0) {
                    const isMatch = allowed.some(d => d && (currentHost.includes(d.toLowerCase()) || d.toLowerCase().includes(currentHost)));
                    if (!isMatch) {
                        return;
                    }
                }
                const payload = (data && (data.activeAutofillPayload || data.manualAssistPayload || data.mecpPayload)) || {};
                
                let effectiveClientId = detail.client_id || null;
                if (!effectiveClientId && payload.client_id) {
                    const capturedPan = (detail.pan || "").trim().toUpperCase();
                    const payloadPan = (payload.pan || "").trim().toUpperCase();
                    if (!capturedPan) {
                        effectiveClientId = detail.arn ? null : payload.client_id;
                    } else if (payloadPan && capturedPan === payloadPan) {
                        effectiveClientId = payload.client_id;
                    } else {
                        effectiveClientId = null;
                    }
                }

                chrome.runtime.sendMessage({
                    type: "filing_result",
                    client_id: effectiveClientId,
                    client_name: detail.client_name || detail.name || detail.taxpayer_name || payload.client_name || payload.name || "",
                    name: detail.client_name || detail.name || detail.taxpayer_name || payload.client_name || payload.name || "",
                    taxpayer_name: detail.client_name || detail.name || detail.taxpayer_name || payload.client_name || payload.name || "",
                    portal: detail.portal || payload.portal || "Portal",
                    arn: detail.arn || "N/A",
                    capture_method: detail.capture_method || "SAD_API_Interceptor",
                    period_label: detail.period_label || payload.period_label || "",
                    filing_type: detail.filing_type || payload.filing_type || "",
                    pan: detail.pan || payload.pan || "",
                    url: detail.url || "",
                    raw_payload: detail.raw_payload || {},
                    simple_parser_evidence: detail.simple_parser_evidence || null,
                    session_id: detail.session_id || ""
                });
            });
        } catch (e) {
            console.warn("Sera Filing Detector: Extension context error:", e);
        }
    });

    // Listen for live toggle changes from background worker and forward killswitch to main world
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
        chrome.runtime.onMessage.addListener((msg) => {
            if (msg.type === "SERA_SAD_STATE_CHANGED" || msg.type === "SERA_TRACKER_STATE_CHANGED") {
                const isEnabled = msg.sadEnabled !== false && msg.trackerEnabled !== false;
                window.postMessage({ type: 'SERA_SAD_KILLSWITCH', enabled: isEnabled }, '*');
            }
        });
    }
})();
