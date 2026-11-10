import re
from pathlib import Path
from core.extractors.text_reader import read_text_file
from core.extractors.doc_reader import (
    read_pdf_first_page, read_docx_first_page, read_odt_first_page,
    read_pptx_first_slide, read_odp_first_slide,
    read_xlsx_date_only, read_ods_date_only
)
from core.extractors.ocr_engine import ocr_image, ocr_scanned_pdf
from core.ai_router import AIRouter
from core.renamer import generate_safe_filename, is_filename_non_explicit
from config import MIN_PDF_TEXT_LENGTH, AI_CONFIG_SIGNATURE

class FileClassifier:
    def __init__(self, ai_router: AIRouter = None, mode: str = None):
        self.ai_router = ai_router or AIRouter(mode=mode)

    def process_file(self, file_info: dict) -> dict:
        """
        Extrait le contenu (texte ou OCR), appelle le routeur d'IA
        et génère les métadonnées de tri, mots-clés, résumé et proposition de nom.
        Règles strictes :
        - PDF : analyse UNIQUEMENT la première page (texte ou OCR).
        - Word (.docx) : analyse UNIQUEMENT la première page.
        - Excel (.xlsx/.xls) : analyse UNIQUEMENT la date (pas de dump de données).
        """
        file_path = Path(file_info["abs_path"])
        family = file_info.get("family")
        ext = file_info.get("ext", "").lower()
        
        extracted_text = ""
        ocr_used = False

        # 1. Extraction de contenu selon le type de fichier
        if family == 'text':
            extracted_text = read_text_file(file_path)

        elif family == 'pdf':
            # Strictement la 1ère page
            pdf_text, num_pages = read_pdf_first_page(file_path)
            extracted_text = pdf_text
            file_info["num_pages"] = num_pages
            
            # Si le texte de la 1ère page est insuffisant -> OCR sur la 1ère page uniquement
            if len(pdf_text.strip()) < MIN_PDF_TEXT_LENGTH:
                ocr_result = ocr_scanned_pdf(file_path, max_pages=1)
                if ocr_result and not ocr_result.startswith("[Erreur"):
                    extracted_text = ocr_result
                    ocr_used = True

        elif ext in ('.docx', '.dot', '.dotx', '.doc'):
            # Documents texte Word / modèles : strictement la 1ère page
            extracted_text = read_docx_first_page(file_path)

        elif ext in ('.odt', '.ott'):
            # Documents texte OpenOffice Writer / modèles : strictement la 1ère page
            extracted_text = read_odt_first_page(file_path)

        elif ext in ('.pptx', '.ppt'):
            # Présentations PowerPoint : strictement la 1ère diapositive (titre)
            slide_text, total_slides = read_pptx_first_slide(file_path)
            extracted_text = slide_text
            file_info["total_slides"] = total_slides

        elif ext in ('.odp', '.otp'):
            # Présentations OpenOffice Impress : strictement la 1ère diapositive (titre)
            slide_text, total_slides = read_odp_first_slide(file_path)
            extracted_text = slide_text
            file_info["total_slides"] = total_slides

        elif ext in ('.xlsx', '.xls'):
            # Tableurs Excel : strictement la date, pas de données
            excel_date = read_xlsx_date_only(file_path)
            if excel_date:
                file_info["date_display"] = excel_date
                clean_d = re.sub(r'[^0-9]', '', excel_date)
                if len(clean_d) >= 8:
                    file_info["date_stamp"] = clean_d[:8]
            extracted_text = f"Classeur Excel : {file_info.get('filename')}. Date document : {excel_date or file_info.get('date_display')}."

        elif ext in ('.ods', '.ots'):
            # Tableurs OpenOffice Calc : strictement la date, pas de données
            ods_date = read_ods_date_only(file_path)
            if ods_date:
                file_info["date_display"] = ods_date
                clean_d = re.sub(r'[^0-9]', '', ods_date)
                if len(clean_d) >= 8:
                    file_info["date_stamp"] = clean_d[:8]
            extracted_text = f"Classeur OpenOffice Calc : {file_info.get('filename')}. Date document : {ods_date or file_info.get('date_display')}."

        elif ext in ('.csv', '.rtf'):
            extracted_text = read_text_file(file_path, max_chars=4000)

        elif family == 'image':
            # Pour une image : on tente d'abord l'OCR si c'est un document textuel
            img_ocr = ocr_image(file_path)
            if img_ocr and not img_ocr.startswith("[Erreur") and len(img_ocr.strip()) > 30:
                extracted_text = img_ocr
                ocr_used = True
            # Le fichier image sera aussi passé directement en multimodalité IA pour les photos de paysages/scènes

        # 2. Analyse via le routeur IA (Google -> Groq -> OpenRouter -> Repli)
        ai_result = self.ai_router.analyze_file(file_info, extracted_text=extracted_text)

        # 3. Mots-clés, catégorie et résumé
        category = ai_result.get("category", "Divers")
        keywords = ai_result.get("keywords", ["Fichier"])
        summary = ai_result.get("summary", "")
        clean_keyword = ai_result.get("clean_name") or (keywords[0] if keywords else "Fichier")
        provider_used = ai_result.get("provider_used", "Inconnu")

        # 4. Génération du nom propre avec la date et anti-écrasement
        suggested_name, is_non_explicit = generate_safe_filename(
            target_dir=file_path.parent,
            clean_keyword=clean_keyword,
            date_stamp=file_info.get("date_stamp", ""),
            extension=ext,
            current_stem=file_info.get("stem", "")
        )

        return {
            **file_info,
            "category": category,
            "keywords": keywords,
            "summary": summary,
            "ocr_used": ocr_used,
            "extracted_text_preview": (extracted_text[:300] + '...') if len(extracted_text) > 300 else extracted_text,
            "suggested_filename": suggested_name,
            "is_non_explicit_name": is_non_explicit,
            "provider_used": provider_used,
            "ai_warnings": ai_result.get("ai_warnings", []),
            "ai_config_signature": AI_CONFIG_SIGNATURE
        }
