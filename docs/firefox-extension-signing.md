# Firefox extension: signing and installing

Release Firefox only runs extensions signed by Mozilla. The Chrome/Edge extension is a self-signed
CRX (`build_tools/sera_extension.pem`) and is unaffected. The Firefox build is signed through
addons.mozilla.org (AMO) as an **unlisted** (self-distributed) add-on: free, automatic, and not
published in the public add-on store.

- Add-on ID: `sera-companion@amanassociates.com` (`sera_extension_firefox/manifest.json`)
- Signing script: `build_tools/sign_firefox.py`
- Output: `package_assets/extension/ProjectSeraCompanion.signed.xpi` (gitignored - keep a copy)

## Every version bump (re-signing)

AMO signs each version number exactly once and rejects a repeat, so every release that changes the
extension needs a new version and a new signature.

1. Bump the version everywhere the build preflight checks: `version.py`, `build_tools/installer_setup.iss`,
   `sera_extension/manifest.json` and `sera_extension_firefox/manifest.json`. All four must agree.
2. Sign (about two minutes, it waits for Mozilla's automatic review):
   ```
   python build_tools/sign_firefox.py
   ```
   It rebuilds the extension, strips the Chromium-only `key` from a temporary copy, submits it with
   `web-ext sign --channel=unlisted`, and writes the signed file to the path above.
3. Check the result: open the `.xpi` as a zip and confirm `manifest.json` has the new version and
   `META-INF/mozilla.rsa` exists.
4. Install it (see below) and confirm `about:addons` shows the new version.

If signing says the version already exists, you forgot step 1 (or already signed this version - the
signed file from that run is still in `package_assets/extension/`, use it).

## One-time setup: AMO API credentials

1. Create credentials at https://addons.mozilla.org/developers/addon/api/key/ ("Generate new credentials").
   You get a JWT issuer (`user:12345:67`) and a 64-character hex JWT secret.
2. Store them as user environment variables, then open a new terminal:
   ```
   setx WEB_EXT_API_KEY "user:12345:67"
   setx WEB_EXT_API_SECRET "<64-hex-secret>"
   ```
3. Never commit them, paste them into chat, or put them in an installer. If one leaks, revoke it on the
   same AMO page and generate a new pair.

Needs Node.js (`npx` downloads `web-ext` on first use).

## Installing the signed build on a PC

Firefox installs it through an enterprise policy, which also makes it undeletable by the user:

1. Copy the signed file to `C:\Users\<user>\AmanAssociates_Sera\sera_extension_firefox.xpi`.
2. Run `tools\maintenance\setup_firefox_permanent_policy.bat` as administrator. It copies the signed
   file there and writes `C:\Program Files\Mozilla Firefox\distribution\policies.json`
   (`installation_mode: normal_installed`). The script currently hard-codes the `Nex` user profile.
3. Restart Firefox completely.

**Updating:** replace `sera_extension_firefox.xpi` with the new signed file and restart Firefox. If
`about:addons` still shows the old version, Remove the add-on there and restart once more; the policy
reinstalls the new file.

## Bridge pairing (trust on first connect)

The desktop app accepts the first Firefox extension that connects and remembers its
`moz-extension://<uuid>` in `~/AmanAssociates_Sera/ws_bridge/firefox_origin.txt`; any other Firefox
extension origin is refused. Firefox gives an installed extension a stable UUID, so a version update
keeps working. Delete that file only when the add-on is removed and reinstalled under a different
UUID (for example when switching from a temporary add-on to the signed one) or on a new Firefox profile.

## Gotchas learned

- Firefox forces `ws://` to `wss://` for Manifest V3 extensions by default, which breaks the app's local
  bridge. `sera_extension_firefox/manifest.json` overrides `content_security_policy.extension_pages`
  to avoid this - do not remove it.
- `browser_specific_settings.gecko.data_collection_permissions` must stay declared or AMO flags the build.
- A temporary add-on (`about:debugging` > Load Temporary Add-on > `sera_extension_firefox\manifest.json`)
  is for testing only and disappears on restart. It cannot coexist with a policy-installed copy of the
  same ID ("corrupt add-on" error).
- Autofill goes only to the browser a service is set to (Browser field in the service dialog); a service
  left on Default prefers the app/OS default browser, then Chrome/Edge, then anything connected.
- The Windows installer does not yet ship or install the Firefox `.xpi`; Firefox is set up per PC with the
  steps above.
