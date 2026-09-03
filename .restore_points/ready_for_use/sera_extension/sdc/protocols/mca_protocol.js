/**
 * mca_protocol.js — SDC MCA V3 Portal Protocol (Stub)
 * ====================================================
 * Crosshairs for the MCA V3 portal (mca.gov.in).
 * To be developed in a subsequent sprint.
 *
 * Planned crosshairs:
 *   mca_company_profile — Company Master page (CIN + Name)
 *   mca_form_filed      — ROC annual form filing success + SRN
 *   mca_challan_paid    — Challan payment confirmation + SRN
 */

(function () {
  'use strict';

  function _register() {
    const SDC = window.__SERA_SDC__;
    if (!SDC) { setTimeout(_register, 100); return; }

    console.log('⚡ Sera SDC: MCA Protocol stub loaded (no crosshairs registered yet).');
  }

  _register();
})();
