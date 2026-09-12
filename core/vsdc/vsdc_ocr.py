"""
core/vsdc/vsdc_ocr.py — DPI-Aware Screen Snapper & Windows Native OCR Pipeline
==============================================================================
Captures target portal window frames via Windows GDI and runs hardware-accelerated
OCR via Windows.Media.Ocr (winrt). Supports targeted regional cropping to minimize
CPU and memory usage down to ~10ms–25ms.
"""

import asyncio
import ctypes
from ctypes import wintypes
import io
import time
from typing import Dict, List, Optional, Tuple, Any
from PIL import Image

import winrt.windows.media.ocr as ocr
import winrt.windows.graphics.imaging as imaging
import winrt.windows.storage.streams as streams

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class VSDCOcrEngine:
    """
    Manages the Windows.Media.Ocr runtime and window capture pipeline.
    """

    def __init__(self):
        self._engine: Optional[ocr.OcrEngine] = None
        self._init_engine()

    def _init_engine(self):
        try:
            self._engine = ocr.OcrEngine.try_create_from_user_profile_languages()
        except Exception as e:
            print(f"⚠️ VSDC: Failed to initialize Windows.Media.Ocr: {e}")
            self._engine = None

    @property
    def is_available(self) -> bool:
        return self._engine is not None

    def capture_window_image(self, hwnd: int) -> Optional[Image.Image]:
        """
        Captures the client area of a specific window HWND into a PIL Image.
        Uses GDI PrintWindow with fallback to screen BitBlt.
        """
        if not hwnd or not user32.IsWindow(hwnd):
            return None

        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top

        if w <= 0 or h <= 0:
            return None

        hdc = user32.GetDC(hwnd)
        if not hdc:
            return None

        memdc = gdi32.CreateCompatibleDC(hdc)
        hbmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(memdc, hbmp)

        # Try PrintWindow with PW_RENDERFULLCONTENT (0x00000002)
        success = user32.PrintWindow(hwnd, memdc, 2)
        if not success:
            # Fallback 1: Window DC BitBlt
            gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)  # SRCCOPY

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

        # Cleanup GDI resources
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(hwnd, hdc)

        img = None
        try:
            img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        except Exception as e:
            print(f"⚠️ VSDC: Image buffer conversion error: {e}")

        # Check if captured image is all-black (common with GPU-accelerated Chromium windows)
        is_blank = False
        if img:
            extrema = img.getextrema()
            if extrema == ((0, 0), (0, 0), (0, 0)):
                is_blank = True

        # Fallback 2: Direct Screen DC BitBlt using client rect in screen coordinates
        if not img or is_blank:
            try:
                pt = wintypes.POINT(0, 0)
                user32.ClientToScreen(hwnd, ctypes.byref(pt))
                screen_hdc = user32.GetDC(0)
                if screen_hdc:
                    memdc = gdi32.CreateCompatibleDC(screen_hdc)
                    hbmp = gdi32.CreateCompatibleBitmap(screen_hdc, w, h)
                    gdi32.SelectObject(memdc, hbmp)
                    gdi32.BitBlt(memdc, 0, 0, w, h, screen_hdc, pt.x, pt.y, 0x00CC0020)
                    gdi32.GetDIBits(screen_hdc, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
                    gdi32.DeleteObject(hbmp)
                    gdi32.DeleteDC(memdc)
                    user32.ReleaseDC(0, screen_hdc)
                    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
            except Exception as e:
                print(f"⚠️ VSDC: Screen capture fallback error: {e}")

        return img

    @staticmethod
    def crop_region(img: Image.Image, region_type: str) -> Image.Image:
        """
        Extracts a targeted region of interest from the browser window image.
        - 'header': Top portion including taxpayer profile pill and portal navigation
        - 'center_card': Center card area (form & period select)
        - 'receipt_card': Center receipt area (Ack receipt, ARN)
        - 'full': Untouched window image
        """
        w, h = img.size
        if region_type == "header":
            top_h = max(180, min(int(h * 0.28), 320))
            return img.crop((0, 0, w, min(top_h, h)))
        elif region_type == "center_card":
            left = int(w * 0.02)
            right = int(w * 0.98)
            top = int(h * 0.10)
            bottom = int(h * 0.90)
            return img.crop((left, top, right, bottom))
        elif region_type == "receipt_card":
            left = int(w * 0.02)
            right = int(w * 0.98)
            top = int(h * 0.08)
            bottom = int(h * 0.92)
            return img.crop((left, top, right, bottom))
        return img

    async def _run_ocr_async(self, img: Image.Image) -> Dict[str, Any]:
        """
        Executes asynchronous OCR via Windows.Media.Ocr.
        """
        if not self._engine:
            return {"text": "", "lines": [], "words": [], "latency_ms": 0.0}

        # Save PIL image to PNG in memory
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw_bytes = buf.getvalue()

        # Load into WinRT stream
        stream = streams.InMemoryRandomAccessStream()
        writer = streams.DataWriter(stream)
        writer.write_bytes(raw_bytes)
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)

        # Decode software bitmap
        decoder = await imaging.BitmapDecoder.create_async(stream)
        bmp = await decoder.get_software_bitmap_async()

        # Run OCR
        t0 = time.perf_counter()
        ocr_result = await self._engine.recognize_async(bmp)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        full_text = ocr_result.text
        lines = [line.text for line in ocr_result.lines]
        words = []
        for line in ocr_result.lines:
            for w in line.words:
                rect = w.bounding_rect
                words.append({
                    "text": w.text,
                    "x": rect.x,
                    "y": rect.y,
                    "width": rect.width,
                    "height": rect.height,
                })

        return {
            "text": full_text,
            "lines": lines,
            "words": words,
            "latency_ms": latency_ms,
        }

    def scan_image(self, img: Image.Image, region_type: str = "full") -> Dict[str, Any]:
        """
        Synchronously crops and scans an image using the Windows.Media.Ocr pipeline.
        Thread-safe and supports execution from within threads with active event loops.
        """
        if not img or not self.is_available:
            return {"text": "", "lines": [], "words": [], "latency_ms": 0.0}

        cropped = self.crop_region(img, region_type)
        try:
            # Check if there is an active event loop in this thread
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(lambda: asyncio.run(self._run_ocr_async(cropped)))
                    return future.result()
            else:
                return asyncio.run(self._run_ocr_async(cropped))
        except Exception as e:
            print(f"[VSDC OCR Error] {e}")
            return {"text": "", "lines": [], "words": [], "latency_ms": 0.0}
