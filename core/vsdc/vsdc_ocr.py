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
        pass

    @property
    def is_available(self) -> bool:
        try:
            return ocr.OcrEngine.try_create_from_user_profile_languages() is not None
        except Exception:
            return False

    def capture_window_image(self, hwnd: int) -> Optional[Image.Image]:
        """
        Captures the client area of a specific window HWND into a PIL Image.
        Optimized for high-speed Chromium rendering: uses direct screen BitBlt
        for foreground windows (< 2ms) with seamless GDI fallbacks.
        """
        if not hwnd or not user32.IsWindow(hwnd):
            return None

        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top

        if w <= 0 or h <= 0:
            return None

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down DIB
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = ctypes.create_string_buffer(w * h * 4)

        # 1. Fast Path: If window is foreground, Screen DC BitBlt directly captures
        # hardware-accelerated Chromium pixels in < 2ms without PrintWindow blanks.
        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd == hwnd or user32.IsChild(hwnd, fg_hwnd) or user32.IsChild(fg_hwnd, hwnd):
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
                    if img and img.getextrema() != ((0, 0), (0, 0), (0, 0)):
                        return img
            except Exception:
                pass

        # 2. Fallback: Window DC PrintWindow / BitBlt
        hdc = user32.GetDC(hwnd)
        if not hdc:
            return None

        memdc = gdi32.CreateCompatibleDC(hdc)
        hbmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(memdc, hbmp)

        success = user32.PrintWindow(hwnd, memdc, 2)
        if not success:
            gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)

        gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(hwnd, hdc)

        try:
            img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
            if img and img.getextrema() != ((0, 0), (0, 0), (0, 0)):
                return img
        except Exception:
            pass

        return None

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
        elif region_type == "top_right_profile":
            # Captures exactly the top right 35% of width and top 25% of height
            # This perfectly isolates the user profile pill on both ITR and GST portals
            # ensuring no stray names or noise are captured from other header elements.
            left = int(w * 0.65)
            bottom = min(250, int(h * 0.25))
            return img.crop((left, 0, w, bottom))
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
        elif region_type == "welcome_dashboard":
            # Captures header, center welcome greeting, preference, and right profile card
            return img.crop((0, 0, w, min(int(h * 0.65), h)))
        elif region_type == "form_details":
            # Captures header, breadcrumb, form banner, and 4-column metadata table precisely
            return img.crop((0, 0, w, min(int(h * 0.52), h)))
        return img

    async def _run_ocr_async(self, img: Image.Image) -> Dict[str, Any]:
        """
        Executes asynchronous OCR via Windows.Media.Ocr.
        Creates OcrEngine inside the async thread context to ensure COM apartment affinity.
        """
        try:
            engine = ocr.OcrEngine.try_create_from_user_profile_languages()
        except Exception:
            engine = None

        if not engine:
            return {"text": "", "lines": [], "words": [], "latency_ms": 0.0}

        # Save PIL image to uncompressed BMP in memory (instant memcpy, 0.2ms vs 50ms PNG)
        buf = io.BytesIO()
        img.save(buf, format="BMP")
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
        ocr_result = await engine.recognize_async(bmp)
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
            # WinRT async completion requires isolation from caller threads that may have
            # initialized COM in Single-Threaded Apartment (STA) mode (e.g. comtypes/UIAutomation).
            # Running in a dedicated worker thread avoids message pump deadlocks.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(self._run_ocr_async(cropped)))
                return future.result()
        except Exception as e:
            print(f"[VSDC OCR Error] {e}")
            return {"text": "", "lines": [], "words": [], "latency_ms": 0.0}
