"""Sign the Firefox extension through addons.mozilla.org (unlisted / self-distributed).

Release Firefox only installs Mozilla-signed add-ons. Signing is free and takes a few minutes,
and the result is a normal .xpi that can be shipped to staff and installed by the policy in
tools/maintenance/setup_firefox_permanent_policy.bat.

One-time setup:
  1. Sign in at https://addons.mozilla.org/developers/addon/api/key/ and create API credentials.
  2. Set them in the environment (never commit them):
         setx WEB_EXT_API_KEY    "user:12345:67"
         setx WEB_EXT_API_SECRET "<64-hex-secret>"
     then open a new terminal.

Usage:  python build_tools/sign_firefox.py

AMO rejects a version it has already signed, so bump "version" in both manifests
(sera_extension and sera_extension_firefox) before every re-sign - see the release process.
Output: package_assets/extension/ProjectSeraCompanion.signed.xpi
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIREFOX_DIR = ROOT / "sera_extension_firefox"
OUTPUT = ROOT / "package_assets" / "extension" / "ProjectSeraCompanion.signed.xpi"


def _stage() -> Path:
    """Copy the extension to a temp dir, minus the Chromium-only "key" field AMO would flag."""
    staged = Path(tempfile.mkdtemp(prefix="sera_ff_sign_")) / "ext"
    shutil.copytree(FIREFOX_DIR, staged, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    manifest_path = staged / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("key", None)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return staged


def main() -> int:
    if not (os.environ.get("WEB_EXT_API_KEY") and os.environ.get("WEB_EXT_API_SECRET")):
        print("Set WEB_EXT_API_KEY and WEB_EXT_API_SECRET first (see this file's docstring).")
        return 2
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        print("Node.js (npx) is required: https://nodejs.org")
        return 2

    # Same synchronisation build_extension.py does, so the signed build matches the CRX.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_extension
    build_extension.build()

    staged = _stage()
    artifacts = staged.parent / "artifacts"
    try:
        cmd = [npx, "--yes", "web-ext", "sign", "--channel=unlisted",
               f"--source-dir={staged}", f"--artifacts-dir={artifacts}"]
        print("Submitting to addons.mozilla.org (this waits for Mozilla's review)...")
        if subprocess.run(cmd).returncode != 0:
            print("Signing failed - see the output above (a version AMO already signed is the usual cause).")
            return 1
        signed = sorted(artifacts.glob("*.xpi"), key=lambda p: p.stat().st_mtime)
        if not signed:
            print("web-ext reported success but produced no .xpi.")
            return 1
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(signed[-1], OUTPUT)
        print(f"Signed: {OUTPUT}")
        return 0
    finally:
        shutil.rmtree(staged.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
