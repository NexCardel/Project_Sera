"""The persistent OCR lane in core/vsdc/vsdc_ocr.

Every scan used to create a thread, an event loop and an OCR engine, and could wait
forever on a WinRT promise. These tests use a fake engine body, no portal, no real OCR.
"""

import time
from unittest.mock import PropertyMock, patch

from PIL import Image

from core.vsdc import vsdc_ocr
from core.vsdc.vsdc_ocr import VSDCOcrEngine

IMG = Image.new("RGB", (64, 64), (255, 255, 255))


def _available():
    return patch.object(VSDCOcrEngine, "is_available",
                       new_callable=PropertyMock, return_value=True)


def test_one_engine_is_built_for_many_scans():
    created = []
    seen = []

    def make_engine():
        created.append(object())
        return created[-1]

    async def fake_scan(self, img, engine=None):
        seen.append(engine)
        return {"text": "ok", "lines": ["ok"], "words": [], "latency_ms": 0.0}

    engine = VSDCOcrEngine()
    with _available(), \
            patch("core.vsdc.vsdc_ocr._create_ocr_engine", side_effect=make_engine), \
            patch.object(VSDCOcrEngine, "_run_ocr_async", fake_scan):
        for _ in range(3):
            assert engine.scan_image(IMG)["text"] == "ok"

    assert len(created) == 1
    assert seen == [created[0]] * 3


def test_a_stuck_scan_returns_instead_of_freezing_the_caller():
    async def slow_scan(self, img, engine=None):
        import asyncio
        await asyncio.sleep(1.0)
        return {"text": "late", "lines": ["late"], "words": [], "latency_ms": 0.0}

    engine = VSDCOcrEngine()
    with _available(), \
            patch("core.vsdc.vsdc_ocr._create_ocr_engine", return_value=object()), \
            patch.object(VSDCOcrEngine, "_run_ocr_async", slow_scan), \
            patch.object(vsdc_ocr, "_OCR_TIMEOUT_SEC", 0.1):
        started = time.monotonic()
        result = engine.scan_image(IMG)
        elapsed = time.monotonic() - started

    assert result["text"] == ""
    assert elapsed < 0.8
