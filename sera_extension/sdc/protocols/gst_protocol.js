/**
 * gst_protocol.js — SDC GST Portal Protocol (Stub)
 * =================================================
 * Crosshairs for the GST portal (gst.gov.in).
 * To be developed in a subsequent sprint.
 *
 * Planned crosshairs:
 *   gst_dashboard      — Dashboard landing (GSTIN + Legal Name capture)
 *   gst_gstr1_filed    — GSTR-1/IFF filed success confirmation + ARN
 *   gst_gstr3b_filed   — GSTR-3B filed success confirmation + ARN
 *   gst_gstr9_filed    — GSTR-9 / GSTR-9C filed confirmation + ARN
 *   gst_cmp08_filed    — CMP-08 filed confirmation + ARN
 */

(function () {
  'use strict';

  function _register() {
    const SDC = window.__SERA_SDC__;
    if (!SDC) { setTimeout(_register, 100); return; }

    // GST Protocol stub — no crosshairs active yet
    // SDC.register({ name: 'GST Portal', hostMatch: /gst\.gov\.in/, crosshairs: [] });

    console.log('⚡ Sera SDC: GST Protocol stub loaded (no crosshairs registered yet).');
  }

  _register();
})();
