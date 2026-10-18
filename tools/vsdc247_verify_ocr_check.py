"""
tools/vsdc247_verify_ocr_check.py - run the VSDC247-V observation on REAL Windows OCR.

Renders each synthetic page in tests/fixtures/vsdc247_verify as a screen-sized image, reads it with the
project's Windows OCR engine, and feeds the lines to the Verifier in the order the fixtures are listed.
Nothing is written to the ledger (the sink is a list) and nothing is written into the repo except the
rendered PNGs, which go to --out (default: a folder under the system temp directory).

Windows only. Prints a PASS/FAIL line per frame against the expected status.

    python tools/vsdc247_verify_ocr_check.py [--out DIR]
"""

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Expected verifier status per fixture, one frame each, in the order run. The two GSTR-1 success frames
# are read in sequence, so the second one saves the ARN.
EXPECTED = (
    ("gst_login_01.json", "login_empty"),
    ("gst_login_02.json", "login_seen"),
    ("gstr1_list_negative_01.json", "no_reading"),
    ("gstr1_submit_success_01.json", "pending_second_read"),
    ("gstr1_submit_success_02.json", "saved"),
    ("gstr3b_submit_success_01.json", "pending_second_read"),
    ("gstr3b_prefiling_negative_01.json", "no_reading"),
    ("gstr3b_list_negative_01.json", "not_registered"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "vsdc247_verify_ocr"))
    args = parser.parse_args()

    from PIL import Image, ImageDraw, ImageFont
    from core.vsdc.vsdc_ocr import VSDCOcrEngine
    from core.vsdc.vsdc247_verify import Verifier

    engine = VSDCOcrEngine()
    if not engine.is_available:
        print("Windows OCR is not available on this machine. Nothing checked.")
        return 0

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 20)
    except OSError:
        font = ImageFont.load_default()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    verifier = Verifier(sink=lambda entry: (rows.append(entry), True)[1], clock=lambda: 1_800_000_000.0)
    fixtures = ROOT / "tests" / "fixtures" / "vsdc247_verify"

    failures = 0
    for name, expected in EXPECTED:
        rec = json.loads((fixtures / name).read_text(encoding="utf-8"))
        img = Image.new("RGB", (1600, 60 + 40 * len(rec["lines"])), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        for i, line in enumerate(rec["lines"]):
            draw.text((30, 30 + i * 40), line, font=font, fill=(20, 20, 20))
        img.save(out / (Path(name).stem + ".png"))

        ocr_lines = engine.scan_image(img, region_type="full").get("lines", []) or []
        got = verifier.observe_frame(7, rec["url"], ocr_lines, today=date.today())["status"]
        ok = got == expected
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name:34} expected={expected:20} got={got}")

    print(f"\nledger rows: {len(rows)}; expected 1 (the GSTR-1 ARN)")
    if len(rows) != 1:
        failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
