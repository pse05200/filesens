import io
from pathlib import Path
from PIL import Image
from config import OCR_LANGUAGE

try:
    import pytesseract
except ImportError:
    pytesseract = None

try:
    import winocr
except ImportError:
    winocr = None

def _run_winocr(img: Image.Image, lang: str = "fra") -> str:
    """Exécute la reconnaissance optique native Windows via winocr."""
    if winocr is None:
        return ""
    try:
        win_lang = "fr" if "fra" in (lang or "").lower() or "fre" in (lang or "").lower() else "en"
        res = winocr.recognize_pil_sync(img, win_lang)
        if isinstance(res, dict) and res.get("text"):
            return res["text"].strip()
    except Exception:
        pass
    return ""

def ocr_image(image_path: Path | str, lang: str = None) -> str:
    """Effectue la reconnaissance optique de caractères (OCR) sur une image locale."""
    p = Path(image_path)
    lang = lang or OCR_LANGUAGE
    try:
        with Image.open(p) as img:
            # Conversion RGB pour compatibilité
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            # 1. Tentative avec pytesseract
            if pytesseract is not None:
                try:
                    text = pytesseract.image_to_string(img, lang=lang).strip()
                    if text:
                        return text
                except Exception:
                    pass

            # 2. Secours natif Windows (winocr)
            native_text = _run_winocr(img, lang=lang)
            if native_text:
                return native_text

            return ""
    except Exception as e:
        return f"[Erreur OCR image: {e}]"

def ocr_scanned_pdf(pdf_path: Path | str, max_pages: int = 1, lang: str = None) -> str:
    """Tente d'extraire les images de la PREMIÈRE page d'un PDF scanné et d'exécuter l'OCR dessus."""
    from pypdf import PdfReader
    p = Path(pdf_path)
    lang = lang or OCR_LANGUAGE
    extracted_text_parts = []
    try:
        reader = PdfReader(str(p))
        if len(reader.pages) == 0:
            return ""
        # On ne traite que la première page
        page = reader.pages[0]
        for img_idx, img_file in enumerate(page.images):
            try:
                img = Image.open(io.BytesIO(img_file.data))
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                txt = ""
                if pytesseract is not None:
                    try:
                        txt = pytesseract.image_to_string(img, lang=lang).strip()
                    except Exception:
                        pass
                if not txt:
                    txt = _run_winocr(img, lang=lang)

                if txt:
                    extracted_text_parts.append(f"[Page 1 - OCR]:\n{txt}")
            except Exception:
                continue
        return "\n\n".join(extracted_text_parts).strip()
    except Exception as e:
        return f"[Erreur OCR PDF scanné: {e}]"
