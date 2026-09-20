import asyncio
import io
from PIL import Image
from PySide6.QtCore import QThread, Signal

class OCRWorker(QThread):
    finished = Signal(str)
    
    def __init__(self, image_path):
        super().__init__()
        self.image_path = image_path
        
    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.extract_text(self.image_path))
            self.finished.emit(result)
        except Exception as e:
            self.finished.emit(f"OCR Error: {e}")
            
    async def extract_text(self, img_path):
        try:
            import winrt.windows.media.ocr as ocr
            import winrt.windows.graphics.imaging as imaging
            import winrt.windows.storage.streams as streams

            img = Image.open(img_path).convert("RGB")
            engine = ocr.OcrEngine.try_create_from_user_profile_languages()
            if not engine:
                return "OCR Engine not available."
                
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            raw_bytes = buf.getvalue()
            
            stream = streams.InMemoryRandomAccessStream()
            writer = streams.DataWriter(stream)
            writer.write_bytes(raw_bytes)
            await writer.store_async()
            writer.detach_stream()
            stream.seek(0)
            
            decoder = await imaging.BitmapDecoder.create_async(stream)
            bmp = await decoder.get_software_bitmap_async()
            ocr_result = await engine.recognize_async(bmp)
            return ocr_result.text
        except Exception as e:
            return f"OCR Failed: {e}"
