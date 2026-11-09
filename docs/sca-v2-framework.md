# SCAv2 framework: the login page is a trigger too

> **Retired 2026-11-07:** the `SCA_UID_PROBE` login-page trigger described here is removed. A UID page's
> picked node now arms SCA (docs/sca-uid-node-plan.md).

Status: proposal, 2026-11-06. No code written. Supersedes nothing: it adds one trigger to the
shipped protocol-v2 design in `docs/sera-clipboard-assist.md` (pull-based, no passwords in arms).

## 1. The gap

SCA today has one trigger: **copy a client id -> arm -> paste on the portal -> password request**.
If the staff member types the id by hand, copies it from somewhere the clipboard watcher misses
(RDP, a clipboard-history paste, a browser that strips the format), or lets the browser autofill
it, there is no arm and `onCandidate` returns `not-armed` / `not-this-client`. The page saw the id
and the desktop knows every id, but nobody connected the two.

## 2. The idea

Add a **second trigger that ends in the same arm**. The login page reports the id (it already
does: `login.js` -> `SCA_MATCH_CANDIDATE`). When the coordinator has no arm for that id, it asks
the desktop "whose id is this?" (`SCA_UID_PROBE`). The desktop looks it up in the same id index
the clipboard watcher uses and, if the id belongs to a client with a portal on *this* host, builds
the same arm and broadcasts it. Everything after that (candidate replay, host check, password
request, SCC confirm card, fill, audit) is the existing code, untouched.

```text
clipboard copy ---------------------------\
                                           >--> _arm_client_services()  --> SCA_ARM_REQUEST --> (existing v2 path)
login page id (typed/pasted/autofilled) --/
   login.js -> coordinator.onCandidate -> SCA_UID_PROBE -> clipboard_watch.handle_uid_probe
```

## 3. What is reused, and what is new

| Piece | Status |
|---|---|
| id index, collisions dropped, auto-refresh (`refresh_index`, `_ensure_index_fresh`) | reused as is |
| arm build with no passwords (`_arm_client_services`, `sca_protocol.build_arm_request`) | reused as is |
| page-side detection (`login.js`, `sca_adapters.js`) | reused; add settle-debounce for typed ids |
| host rule (`host_matches`, approved domains, `pickService`) | reused as is |
| candidate replay after a late arm (`rememberCandidate`/`replayCandidates`) | reused; it already holds the id for 15 s, which is exactly the probe round trip |
| SCC confirm card for unverified Income Tax passwords, fill, audit log, SCK hand-off | reused as is |
| `SCA_UID_PROBE` message | **new** (constant + dispatch + signal) |
| `ClipboardWatchService.handle_uid_probe(msg)` | **new**, about 20 lines |
| coordinator: send the probe in the two "no arm" branches | **new**, about 15 lines |

Estimated total: roughly 60 lines plus tests; no new module, no new storage, no new port. The JS
lives only in `sera_extension/sca/sca_coordinator.js` (`build_extension.py` copies it to Firefox).

## 4. The probe, precisely

**Extension -> desktop** `SCA_UID_PROBE {candidate, page_host, tab_id?}`, sent by `onCandidate` when:
- SCA is enabled and no assist card is open in that tab (existing checks), **and**
- the frame host passes `isApprovedDomain`, **and**
- the id is not the live arm's id (`candidateMatches` false) or there is no arm, **and**
- the same `tab:frame:candidate` was not probed in the last 10 s.

Never probed: password boxes (login.js already skips them), ids on non-approved hosts.

**Desktop** `handle_uid_probe(msg)`, in this order, each failure is a silent no-op (no reply, so
a page can never use it to ask "is this a client id?"):
1. SCA enabled; vault unlocked.
2. `normalize_uid` + `looks_like_uid`; `_ensure_index_fresh`; `client_id = _uid_index.get(...)`.
3. not `is_suppressed` (MECP/SMTI open) and not in `_paused_clients`.
4. at least one of the client's services has a login link whose host `host_matches(page_host)`.
   This is the check that keeps the arm from existing at all for the wrong site.
5. skip if the live arm is already this client (avoids churn; the arm stays valid).
6. `_arm_client_services(client_id, str(client_id), candidate)` with `trigger="page"`.

**Typing, not just pasting.** `login.js` fires on every `input`, and `RE_ID_TOKEN` accepts any 3+
character alphanumeric string, so typing "GSTU" would probe four times. Fix on the page side: a
paste or autofill probes at once; typed input probes after ~700 ms idle, or on blur/Tab/Enter.

## 5. Safety rules (unchanged, plus three)

Unchanged: arms never carry passwords; the desktop re-checks arm, expiry, uses and host at grant
time; unverified Income Tax passwords need the card click; the reply goes only to the asking
socket; nothing about contents is logged.

New:
1. **No oracle.** The probe has no reply. The only observable effect is an arm delivered to the
   extension background, never to page script.
2. **Arm only for the page's own portal** (step 4 above), so a probe from the GST page cannot arm a
   client that has no GST service.
3. **Page arms do not feed the "armed 5 times, never filled" pause.** That guardrail exists for
   stray clipboard copies; a page arm is by definition followed by a login attempt.

Decision for you: should a page-triggered arm in *autofill* mode fill silently, as a clipboard arm
does? Recommendation: **yes for verified passwords, but show a one-line "Client: ABC Traders"
chip** (see 6.1), because a typed id is the case where a typo can pick the wrong client.

## 6. More improvements for SCAv2

Ranked by value for the effort.

1. **Confirmation chip on every fill.** A 2-second card on the portal page, "Filled for ABC Traders
   (CLI-00370)". Protects against wrong-client fills from typo'd ids and gives staff the ambient
   proof that Sera did the work. Reuses `showWidgetInPage`.
2. **Shared ids, resolved instead of dropped.** `refresh_index` drops any id used by two clients,
   so SCA silently does nothing for them. Keep them as a list; when a probe or copy hits one, show
   a two-button picker card. Biggest "why didn't it work" source after unverified IT passwords.
3. **Why-didn't-it-fill trail.** The coordinator's status strings (`not-armed`, `blocked:...`,
   `not-a-portal-of-this-client`) never leave the browser. Report the last one per tab to
   SCA Diagnostics (`ui/dialogs/sca_diagnostics_dialog.py`) so "SCA did nothing" becomes a visible
   reason. Costs one `SCA_STATE`-style message.
4. **Clipboard hygiene on both triggers.** `update_extension_settings` already carries
   `clipboard_clear_seconds`; apply it after a fill too, and clear a copied *password* (SCC
   combinations) when the page arm takes over.
5. **Wrong-password -> SCK, by page id.** `client_for_portal` / `client_for_arm` let SCK name the
   client after a failed login. With page arms the arm exists for hand-typed logins as well, so
   SCK's wrong-password flow now covers them. No new code; just add a test.
6. **Multi-GSTIN clients.** One PAN, several GSTINs: the id typed on the page should choose the
   service, not just the host. Match the typed id against each service's `userid_column_id` value
   before falling back to host-only.
7. **Re-login in the same tab.** Today `busy` clears only on a new arm. After a page arm, clear the
   tab's `busy` keys on navigation to a login URL, so logging out and into the next client in one
   tab never needs a second copy.
8. **Per-portal adapters** from `sca-redesign-blueprint.md` section 8 (GST, Income Tax, TRACES,
   MCA): worth doing only when a portal changes its login page; `findField` in `sera_dom.js` is
   enough until then.
9. **Usage counters** (fills by portal, trigger = clipboard / page, denials by reason) in the
   existing audit log, so you can see whether the page trigger is carrying its weight.
10. **Browsers without the extension (later, optional).** The SGT UIA reader already sees the
    login window. It could detect the id on screen and put the password on the clipboard with the
    auto-clear timer. Out of scope now; and per the VSDC risk review it must never click the portal.

## 7. Build order

1. `sca_protocol.py`: `MSG_SCA_UID_PROBE`, arm field `trigger`.
2. `ClipboardWatchService.handle_uid_probe` + `ws_bridge.dispatch` branch/signal + one
   `main.py` connect line (next to `sca_password_requested`).
3. Coordinator: probe in the `not-armed` and `not-this-client` branches; `login.js` typed-input
   settle debounce.
4. Tests: `tests/test_sca_v2.py` (probe accepted/ignored matrix), `tests/js/test_sca_coordinator.js`
   (probe sent once, only on approved hosts, replay fills after the arm).
5. Improvements 6.1 and 6.2, then 6.3.

Needs the extension reloaded and browser + app restarted, as with the last SCA change.

## 8. Naming note

"Protocol v2" already names the shipped pull-based design, and `sca-redesign-blueprint.md` calls
its proposal "SCA 3.0". The probe is additive, so the wire stays `protocol_version: 2`; "SCAv2" in
conversation can mean this upgrade, but changelog entries should say "page trigger".
