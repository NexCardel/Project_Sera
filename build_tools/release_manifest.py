"""
Writes installer_output/version.json.next for the installer just built - the version.json to
commit to main AFTER the installer is uploaded as GitHub release v<version>.

The silent update agent (build_tools/updater/sera_update_agent.ps1) installs only an installer
whose SHA-256 matches version.json's "sha256", so the feed must be generated from the exact file
that was uploaded.

    venv\\Scripts\\python build_tools\\release_manifest.py "Release notes text"
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from version import APP_VERSION, GITHUB_REPO  # noqa: E402


def main() -> int:
    notes = sys.argv[1] if len(sys.argv) > 1 else f"Project Sera v{APP_VERSION}"
    name = f"Amas_Sera_Setup_v{APP_VERSION}.exe"
    installer = ROOT / "installer_output" / name
    if not installer.exists():
        print(f"{installer} not found - build the installer first.")
        return 1
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    current = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    feed = {
        "version": APP_VERSION,
        "min_required_version": current.get("min_required_version", APP_VERSION),
        "mandatory": False,
        "download_url": f"https://github.com/{GITHUB_REPO}/releases/download/v{APP_VERSION}/{name}",
        "sha256": digest,
        "release_notes": notes,
    }
    out = ROOT / "installer_output" / "version.json.next"
    out.write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}\n  sha256 {digest}\nCopy it over version.json only after release v{APP_VERSION} is published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
