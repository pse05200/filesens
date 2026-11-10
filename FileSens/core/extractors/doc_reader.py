import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from pypdf import PdfReader
import docx
import openpyxl
import datetime
import re

def read_pdf_first_page(file_path: Path | str) -> tuple[str, int]:
    """
    Extrait STRICTEMENT la première page d'un fichier PDF ainsi que son nombre total de pages.
    Ne lit aucune page ultérieure.
    """
    p = Path(file_path)
    try:
        reader = PdfReader(str(p))
        total_pages = len(reader.pages)
        if total_pages == 0:
            return "", 0
        first_page_text = reader.pages[0].extract_text() or ""
        return first_page_text.strip(), total_pages
    except Exception:
        return "", 0

def read_docx_first_page(file_path: Path | str, max_chars: int = 2500) -> str:
    """
    Extrait STRICTEMENT la première page d'un document Word .docx / .dotx / .dot.
    S'arrête dès qu'un saut de page est rencontré ou au-delà d'une page type (~2500 caractères).
    """
    p = Path(file_path)
    try:
        doc = docx.Document(str(p))
        paragraphs = []
        char_count = 0
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                paragraphs.append(text)
                char_count += len(text)

            # Vérifie les sauts de page dans le XML du paragraphe
            has_break = any(
                'lastRenderedPageBreak' in run._r.xml or 'w:type="page"' in run._r.xml
                for run in para.runs
            )
            if has_break or char_count >= max_chars:
                break

        return "\n".join(paragraphs).strip()
    except Exception:
        # Fallback pour anciens formats .dot binaires ou doc
        try:
            with open(p, 'rb') as f:
                raw = f.read(5000)
            clean = re.sub(rb'[^\x20-\x7E\xC0-\xFF]+', b' ', raw).decode('latin-1', errors='ignore')
            words = [w for w in clean.split() if len(w) > 3]
            return " ".join(words[:150])
        except Exception as e:
            return f"[Erreur lecture docx/dot: {e}]"

def read_odt_first_page(file_path: Path | str, max_chars: int = 2500) -> str:
    """
    Extrait STRICTEMENT la première page d'un document OpenOffice / LibreOffice Writer (.odt, .ott).
    S'arrête à ~1 page (~2500 caractères).
    """
    p = Path(file_path)
    try:
        with zipfile.ZipFile(str(p), 'r') as z:
            with z.open('content.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
                texts = []
                char_count = 0
                for elem in root.iter():
                    if elem.tag.endswith('}p') or elem.tag.endswith('}h'):
                        full_txt = "".join(elem.itertext()).strip()
                        if full_txt:
                            texts.append(full_txt)
                            char_count += len(full_txt)
                        if char_count >= max_chars:
                            break
                return "\n".join(texts).strip()
    except Exception as e:
        return f"[Erreur lecture odt: {e}]"

import html

def read_pptx_first_slide(file_path: Path | str) -> tuple[str, int]:
    """
    Extrait le texte STRICTEMENT de la première diapositive (slide 1 / diapo de titre)
    d'une présentation PowerPoint .pptx ou .ppt, ainsi que le nombre total de diapositives.
    Optimisé pour une vitesse quasi-instantanée (~1-2 ms) par lecture directe du flux XML sans charger l'arbre DOM complet.
    """
    p = Path(file_path)
    ext = p.suffix.lower()

    # 1. Cas PowerPoint moderne .pptx (archive ZIP)
    if ext == '.pptx':
        try:
            with zipfile.ZipFile(str(p), 'r') as z:
                namelist = z.namelist()
                # Comptage immédiat de toutes les diapositives
                slide_files = [n for n in namelist if re.match(r'ppt/slides/slide\d+\.xml', n)]
                total_slides = len(slide_files)

                # Lecture directe et ciblée de la diapositive 1 uniquement
                target_slide = 'ppt/slides/slide1.xml'
                if target_slide in namelist:
                    raw_xml = z.read(target_slide).decode('utf-8', errors='ignore')
                    # Extraction ultra-rapide des balises <a:t>
                    text_matches = re.findall(r'<a:t[^>]*>(.*?)</a:t>', raw_xml)
                    clean_texts = [html.unescape(t.strip()) for t in text_matches if t.strip()]
                    return "\n".join(clean_texts).strip(), total_slides
        except Exception:
            pass

    # 2. Cas ancien PowerPoint binaire .ppt (Office 97-2003)
    if ext == '.ppt':
        try:
            with open(p, 'rb') as f:
                header = f.read(65536)
            text_chunks = []
            # Recherche des chaînes UTF-16LE ou ASCII
            utf16_matches = re.findall(b'(?:[\x20-\x7E\xC0-\xFF]\x00){4,}', header)
            for m in utf16_matches:
                try:
                    t = m.decode('utf-16le').strip()
                    if len(t) > 3 and not any(kw in t for kw in ['Times New Roman', 'Arial', 'Calibri', 'PowerPoint']):
                        text_chunks.append(t)
                except Exception:
                    pass
            if not text_chunks:
                ascii_matches = re.findall(b'[\x20-\x7E]{4,}', header)
                for m in ascii_matches:
                    t = m.decode('latin-1').strip()
                    if len(t) > 3 and not any(kw in t for kw in ['Font', 'Microsoft', 'Current User']):
                        text_chunks.append(t)
            return "\n".join(text_chunks[:8]).strip(), 1
        except Exception:
            pass

    return "", 0

def read_odp_first_slide(file_path: Path | str) -> tuple[str, int]:
    """
    Extrait le texte STRICTEMENT de la première diapositive (slide 1 / diapo de titre)
    d'une présentation OpenOffice / LibreOffice Impress (.odp, .otp), ainsi que le nombre total de diapos.
    Ne lit aucune diapositive ultérieure.
    """
    p = Path(file_path)
    try:
        with zipfile.ZipFile(str(p), 'r') as z:
            with z.open('content.xml') as f:
                tree = ET.parse(f)
                root = tree.getroot()
                pages = [elem for elem in root.iter() if elem.tag.endswith('}page')]
                total_slides = len(pages)
                if total_slides == 0:
                    return "", 0
                first_page = pages[0]
                texts = []
                for elem in first_page.iter():
                    if elem.tag.endswith('}p') or elem.tag.endswith('}h'):
                        t = "".join(elem.itertext()).strip()
                        if t and t not in texts:
                            texts.append(t)
                return "\n".join(texts).strip(), total_slides
    except Exception as e:
        return f"[Erreur lecture odp: {e}]", 0

def read_xlsx_date_only(file_path: Path | str) -> str:
    """
    Pour les fichiers Excel : extrait UNIQUEMENT la date.
    Ne lit PAS le contenu des données du tableur.
    Priorité : métadonnées du fichier Excel, puis première date trouvée en en-tête.
    """
    p = Path(file_path)
    try:
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        props = wb.properties
        if props:
            if props.modified:
                return props.modified.strftime("%Y-%m-%d")
            if props.created:
                return props.created.strftime("%Y-%m-%d")

        if wb.sheetnames:
            sheet = wb[wb.sheetnames[0]]
            for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
                if row_idx > 15:
                    break
                for cell in row:
                    if isinstance(cell, (datetime.date, datetime.datetime)):
                        return cell.strftime("%Y-%m-%d")
                    elif isinstance(cell, str):
                        match = re.search(r'\b(\d{4}[/-]\d{2}[/-]\d{2}|\d{2}[/-]\d{2}[/-]\d{4})\b', cell)
                        if match:
                            return match.group(1)
    except Exception:
        pass
    return ""

def read_ods_date_only(file_path: Path | str) -> str:
    """
    Pour les tableurs OpenOffice / LibreOffice Calc (.ods, .ots) : extrait UNIQUEMENT la date.
    Ne lit PAS les données du tableur.
    Priorité : métadonnées meta.xml (dc:date, meta:creation-date), puis cellule date dans content.xml.
    """
    p = Path(file_path)
    try:
        with zipfile.ZipFile(str(p), 'r') as z:
            # 1. Lire meta.xml
            if 'meta.xml' in z.namelist():
                with z.open('meta.xml') as f:
                    tree = ET.parse(f)
                    for elem in tree.iter():
                        if elem.tag.endswith('date') or elem.tag.endswith('creation-date'):
                            val = elem.text or ""
                            m = re.search(r'(\d{4}-\d{2}-\d{2})', val)
                            if m:
                                return m.group(1)

            # 2. Chercher dans les premières cellules de content.xml
            if 'content.xml' in z.namelist():
                with z.open('content.xml') as f:
                    tree = ET.parse(f)
                    for elem in tree.iter():
                        for k, v in elem.attrib.items():
                            if 'date' in k.lower():
                                m = re.search(r'(\d{4}-\d{2}-\d{2})', v)
                                if m:
                                    return m.group(1)
                        txt = "".join(elem.itertext()).strip()
                        m = re.search(r'(\d{4}[/-]\d{2}[/-]\d{2}|\d{2}[/-]\d{2}[/-]\d{4})', txt)
                        if m:
                            return m.group(1)
    except Exception:
        pass
    return ""
