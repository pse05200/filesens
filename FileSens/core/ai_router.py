import json
import base64
import re
import os
from pathlib import Path
from PIL import Image
import requests

from config import (
    MAIA_API_KEYS, MAIA_BASE_URL, MAIA_TEXT_MODEL, MAIA_VISION_MODEL, MAIA_TIMEOUT,
    GEMINI_API_KEYS, GROQ_API_KEYS, OPENROUTER_API_KEYS, DEEPSEEK_API_KEYS,
    GEMINI_MODEL, GROQ_VISION_MODEL, GROQ_TEXT_MODEL, OPENROUTER_MODEL, DEEPSEEK_MODEL,
    DEFAULT_CATEGORIES
)

class KeyRotator:
    """Gère un pool de clés API avec rotation automatique en cas d'erreur de quota."""
    def __init__(self, keys: list[str]):
        self.keys = keys
        self.current_idx = 0
        self.disabled_indices = set()

    @property
    def total_count(self) -> int:
        return len(self.keys)

    @property
    def available_count(self) -> int:
        return len(self.keys) - len(self.disabled_indices)

    @property
    def has_keys(self) -> bool:
        return self.available_count > 0

    def get_current(self) -> str | None:
        if not self.has_keys:
            return None
        if self.current_idx in self.disabled_indices:
            self.rotate()
        return self.keys[self.current_idx] if self.has_keys else None

    def rotate(self) -> str | None:
        if not self.has_keys:
            return None
        for offset in range(1, len(self.keys) + 1):
            next_idx = (self.current_idx + offset) % len(self.keys)
            if next_idx not in self.disabled_indices:
                self.current_idx = next_idx
                break
        return self.keys[self.current_idx] if self.has_keys else None

    def disable_current(self) -> None:
        if self.has_keys:
            self.disabled_indices.add(self.current_idx)
            self.rotate()


def clean_json_response(raw_text: str) -> dict:
    """Extrait et nettoie un objet JSON depuis une réponse de modèle LLM."""
    text = raw_text.strip()
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        # Tente de trouver la première accolade ouvrante et fermante
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            text = text[start:end+1]
    return json.loads(text)


def has_specific_content_keywords(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    keywords = result.get("keywords")
    if not isinstance(keywords, list) or len(keywords) not in (2, 3):
        return False
    normalized = [word.strip().casefold() for word in keywords if isinstance(word, str)]
    generic = {
        "pdf", "document", "document pdf", "fichier", "fichier pdf", "texte",
        "inconnu", "photo", "photographie", "image", "fichier image",
        "bureautique", "fichier bureautique", "document word", "word",
        "powerpoint", "présentation", "presentation", "tableur", "classeur excel",
    }
    return len(normalized) in (2, 3) and all(normalized) and len(set(normalized)) == len(normalized) and not any(
        word in generic for word in normalized
    )


def encode_image_base64(image_path: Path | str, max_dimension: int = 1600) -> str:
    """Redimensionne si besoin et encode une image en base64."""
    p = Path(image_path)
    with Image.open(p) as img:
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        # Redimensionnement léger pour limiter la taille de payload
        if max(img.width, img.height) > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        
        import io
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')


class AIRouter:
    def __init__(self, mode: str = None):
        from config import FILESENS_MODE
        self.mode = (mode or FILESENS_MODE or "auto").strip().lower()
        self.maia_rotator = KeyRotator(MAIA_API_KEYS)
        self.deepseek_rotator = KeyRotator(DEEPSEEK_API_KEYS)
        self.gemini_rotator = KeyRotator(GEMINI_API_KEYS)
        self.groq_rotator = KeyRotator(GROQ_API_KEYS)
        self.openrouter_rotator = KeyRotator(OPENROUTER_API_KEYS)
        self.warnings = []

    def should_use_maia(self) -> bool:
        """
        Détermine si Maïa Chat CEA doit être utilisé :
        - 'external' : JAMAIS (mode API externe forcé, bouclage sur les clés .env uniquement)
        - 'maia'     : OUI si clés disponibles
        - 'auto'     : OUI si nous sommes sur le site/réseau CEA et que Maïa est joignable,
                       NON si nous sommes sur un serveur VPS ou hors du réseau CEA.
        """
        if self.mode == "external":
            return False
        if not self.maia_rotator.has_keys:
            return False
        if self.mode == "maia":
            return True
        try:
            from core.environment import is_cea_network, is_maia_reachable
            return is_cea_network() and is_maia_reachable(timeout_s=1.0)
        except Exception:
            return False

    def _record_failure(
        self,
        provider: str,
        status_code: int | None,
        key_number: int,
        rotator: KeyRotator,
        error: Exception | None = None
    ) -> None:
        err_msg = str(error or "").lower()
        total_keys = rotator.total_count
        key_label = f"la clé {key_number}" if total_keys <= 1 else f"la clé {key_number}/{total_keys}"
        is_daily_quota = ("resource_exhausted" in err_msg or "daily" in err_msg or "per day" in err_msg or "quota exceeded" in err_msg)

        if status_code == 402:
            message = f"{provider} : crédit épuisé pour {key_label}. Passage à la clé ou au fournisseur suivant."
            rotator.disable_current()
        elif status_code in (401, 403):
            message = f"{provider} : {key_label} refusée (HTTP {status_code})."
            rotator.disable_current()
        elif status_code == 429 and is_daily_quota:
            message = f"{provider} : quota journalier épuisé pour {key_label} (HTTP 429). Passage à la clé ou au fournisseur suivant."
            rotator.disable_current()
        elif status_code == 429:
            message = f"{provider} : limite de requêtes atteinte pour {key_label} (HTTP 429)."
            rotator.rotate()
        elif status_code == 404:
            message = f"{provider} : modèle ou endpoint introuvable (HTTP 404)."
            rotator.rotate()
        elif error is not None and (
            isinstance(error, (requests.Timeout, TimeoutError))
            or "timeout" in err_msg
            or "timed out" in err_msg
        ):
            message = f"{provider} : délai d'attente dépassé (timeout) pour {key_label}."
            rotator.rotate()
        elif status_code is None:
            message = f"{provider} : connexion ou appel API impossible pour {key_label}."
            rotator.rotate()
        else:
            message = f"{provider} : appel refusé (HTTP {status_code}) pour {key_label}."
            rotator.rotate()

        if message not in self.warnings:
            self.warnings.append(message)

    @staticmethod
    def _exception_status(error: Exception) -> int | None:
        for attribute in ("status_code", "code"):
            value = getattr(error, attribute, None)
            if isinstance(value, int):
                return value
        msg = str(error)
        match = re.search(r'\b(40[0-9]|429|403|401|402|50[0-9])\b', msg)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None

    def _accept_result(self, result: dict | None, provider: str, file_info: dict) -> dict | None:
        if not isinstance(result, dict):
            return None
        if file_info.get("family") in {"pdf", "image", "document"} and not has_specific_content_keywords(result):
            message = f"{provider} : réponse sans deux mots-clés précis ; essai du fournisseur suivant."
            if message not in self.warnings:
                self.warnings.append(message)
            return None
        result["provider_used"] = provider
        result["ai_warnings"] = list(self.warnings)
        if not isinstance(result.get("key_entities"), list) or not result["key_entities"]:
            summary_lines = [l.strip().lstrip("-•* ") for l in (result.get("summary") or "").split("\n") if l.strip().lstrip("-•* ")]
            result["key_entities"] = summary_lines if summary_lines else list(result.get("keywords") or [])
        return result

    def analyze_file(self, file_info: dict, extracted_text: str = "") -> dict:
        """
        Orchestre l'analyse via la cascade multi-clés :
        1. Maïa Chat CEA (Mistral Small - Priorité réseau CEA si mode auto ou maia)
        2. Cascade externe :
           - Pour les images : Google Gemini -> Groq -> OpenRouter -> DeepSeek
           - Pour le texte   : DeepSeek -> Google Gemini -> Groq -> OpenRouter
        3. Repli heuristique local si toutes les clés sont épuisées
        """
        self.warnings = []
        is_image = file_info.get("family") == "image"

        # 1. Essai Maïa CEA (Souverain interne - Priorité réseau CEA)
        if self.should_use_maia():
            result = self._accept_result(
                self._try_maia(file_info, extracted_text, is_image), "Maïa CEA", file_info
            )
            if result:
                return result

        # 2. Cascade selon le type de fichier
        if is_image:
            # Pour les images, Google Gemini (multimodal) et Groq Vision sont prioritaires
            if self.gemini_rotator.has_keys:
                result = self._accept_result(
                    self._try_gemini(file_info, extracted_text, is_image), "Google Gemini", file_info
                )
                if result:
                    return result

            if self.groq_rotator.has_keys:
                result = self._accept_result(
                    self._try_groq(file_info, extracted_text, is_image), "Groq", file_info
                )
                if result:
                    return result

            if self.openrouter_rotator.has_keys:
                result = self._accept_result(
                    self._try_openrouter(file_info, extracted_text, is_image), "OpenRouter", file_info
                )
                if result:
                    return result

            if self.deepseek_rotator.has_keys:
                result = self._accept_result(
                    self._try_deepseek(file_info, extracted_text, is_image), "DeepSeek", file_info
                )
                if result:
                    return result

        else:
            # Pour les documents textes et PDF, DeepSeek est rapide, très sobre et économique
            if self.deepseek_rotator.has_keys:
                result = self._accept_result(
                    self._try_deepseek(file_info, extracted_text, is_image), "DeepSeek", file_info
                )
                if result:
                    return result

            if self.gemini_rotator.has_keys:
                result = self._accept_result(
                    self._try_gemini(file_info, extracted_text, is_image), "Google Gemini", file_info
                )
                if result:
                    return result

            if self.groq_rotator.has_keys:
                result = self._accept_result(
                    self._try_groq(file_info, extracted_text, is_image), "Groq", file_info
                )
                if result:
                    return result

            if self.openrouter_rotator.has_keys:
                result = self._accept_result(
                    self._try_openrouter(file_info, extracted_text, is_image), "OpenRouter", file_info
                )
                if result:
                    return result

        # 3. Repli local sans IA externe
        result = self._heuristic_fallback(file_info, extracted_text)
        result["ai_warnings"] = list(self.warnings)
        return result

    def _build_prompt(self, file_info: dict, extracted_text: str, is_image: bool) -> str:
        prompt = f"""Tu es un assistant expert en analyse, classification et organisation de fichiers.
Analyse le fichier suivant :
- Nom d'origine : {file_info.get('filename')}
- Type : {file_info.get('family')} ({file_info.get('ext')})
- Taille : {file_info.get('size_formatted')}
"""
        if extracted_text:
            preview = extracted_text[:4000]
            prompt += f"\nContenu extrait du fichier :\n\"\"\"\n{preview}\n\"\"\"\n"

        family = file_info.get("family")
        if family == "image":
            keyword_instruction = (
                "Fournis exactement 2 mots-clés (étiquettes) distincts et précis (jusqu'à 3 étiquettes principales) décrivant le sujet réel "
                "de l'image (ex: 'Observatoire_Solaire', 'Paysage_Montagne', 'Schema_Technique'). "
                "N'utilise jamais 'Photo', 'Image' ou 'Photographie' comme mot-clé."
            )
        elif family in {"pdf", "document"}:
            keyword_instruction = (
                "Fournis exactement 2 mots-clés (étiquettes) distincts et précis (jusqu'à 3 étiquettes principales) sur le sujet réel du document "
                "d'après le contenu extrait (ex: 'Bilan_Financier', 'Rapport_Activite', 'Transition_Energetique'). "
                "N'utilise jamais son format technique comme mot-clé ('PDF', 'Word', 'Document', etc.). "
                "Si le contenu est insuffisant, utilise les indices du nom du fichier."
            )
        else:
            keyword_instruction = (
                "Fournis exactement 2 mots-clés (étiquettes) parlants et précis sur le contenu (ex: 'Facture_Energie', 'Releve_Bancaire')."
            )
        prompt += f"""
Tâches :
1. Détermine la catégorie la plus adaptée parmi : {', '.join(DEFAULT_CATEGORIES)} (ou une catégorie courte).
2. {keyword_instruction}
3. Extrais une dizaine de mots-clés ou entités clés concrets et spécifiques (noms propres, projets, sigles, institutions, thématiques réelles, ex: 'OZO', 'Aix-Marseille Université', 'Formation Professeurs'). Ne rédige AUCUN résumé verbeux inutile du genre 'L'image présente' ou 'Ce document est'.
4. Formate une liste sobre de puces directes avec ces entités et faits clés concrets (sans phrase d'introduction).
5. Propose un identifiant / étiquette clé court et propre (ex: 'Observatoire_Solaire').

Réponds UNIQUEMENT avec un objet JSON strict valide dans ce format exact :
{{
  "category": "Nom de la catégorie",
  "keywords": ["Etiquette_1", "Etiquette_2", "Etiquette_3"],
  "key_entities": ["Entite_1", "Entite_2", "Entite_3", "Entite_4", "Entite_5", "Entite_6", "Entite_7", "Entite_8"],
  "summary": "- Entite_1 ou fait marquant\\n- Entite_2 ou fait marquant\\n- Entite_3",
  "clean_name": "Identifiant_Cle"
}}
"""
        return prompt

    def _try_maia(self, file_info: dict, extracted_text: str, is_image: bool) -> dict | None:
        """
        Appel à l'API interne Maïa Chat CEA (Mistral).
        Utilise Mistral Small pour le texte et les images.
        """
        prompt = self._build_prompt(file_info, extracted_text, is_image)
        content = prompt
        model_name = MAIA_VISION_MODEL if is_image else MAIA_TEXT_MODEL

        if is_image:
            try:
                image_data = encode_image_base64(file_info["abs_path"])
                mime = file_info.get("mime") or "image/jpeg"
            except (OSError, ValueError, KeyError):
                self.warnings.append("Maïa CEA : image illisible pour l'analyse visuelle.")
                return None
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
            ]

        body = {
            "model": model_name,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2,
            "max_tokens": 800,
        }

        url = f"{MAIA_BASE_URL.rstrip('/')}/chat/completions"

        for _ in range(self.maia_rotator.available_count):
            key = self.maia_rotator.get_current()
            if not key:
                break
            key_number = self.maia_rotator.current_idx + 1
            try:
                response = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=MAIA_TIMEOUT,
                )
                if response.status_code != 200:
                    self._record_failure("Maïa CEA", response.status_code, key_number, self.maia_rotator)
                    continue
                data = response.json()
                raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(raw, str) and raw.strip():
                    parsed = clean_json_response(raw)
                    # Normalisation des mots-clés : garantir 2 mots-clés uniques si le modèle en a fourni plus
                    if isinstance(parsed, dict) and isinstance(parsed.get("keywords"), list):
                        kws = [str(k).strip() for k in parsed["keywords"] if str(k).strip()]
                        if len(kws) > 2:
                            seen = set()
                            dedup = []
                            for k in kws:
                                if k.lower() not in seen:
                                    seen.add(k.lower())
                                    dedup.append(k)
                                    if len(dedup) == 2:
                                        break
                            parsed["keywords"] = dedup
                    return parsed
                message = "Maïa CEA : réponse vide ; essai de la clé ou du fournisseur suivant."
                if message not in self.warnings:
                    self.warnings.append(message)
                self.maia_rotator.rotate()
            except (ValueError, KeyError, IndexError, TypeError):
                message = "Maïa CEA : format de réponse JSON invalide ; essai du fournisseur suivant."
                if message not in self.warnings:
                    self.warnings.append(message)
                self.maia_rotator.rotate()
            except Exception as error:
                self._record_failure(
                    "Maïa CEA", self._exception_status(error), key_number, self.maia_rotator, error=error
                )
        return None

    def _try_deepseek(self, file_info: dict, extracted_text: str, is_image: bool) -> dict | None:
        prompt = self._build_prompt(file_info, extracted_text, is_image)
        content = prompt
        if is_image:
            try:
                image_data = encode_image_base64(file_info["abs_path"])
            except (OSError, ValueError, KeyError):
                self.warnings.append("DeepSeek : image illisible pour l'analyse visuelle.")
                return None
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            ]
        body = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": 600,
            "temperature": 0.2,
        }
        for _ in range(self.deepseek_rotator.available_count):
            key = self.deepseek_rotator.get_current()
            if not key:
                break
            key_number = self.deepseek_rotator.current_idx + 1
            try:
                response = requests.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=body,
                    timeout=30,
                )
                if response.status_code != 200:
                    self._record_failure("DeepSeek", response.status_code, key_number, self.deepseek_rotator)
                    continue
                raw = response.json()["choices"][0]["message"]["content"]
                if isinstance(raw, str) and raw.strip():
                    return clean_json_response(raw)
                message = "DeepSeek : réponse vide ; essai de la clé ou du fournisseur suivant."
                if message not in self.warnings:
                    self.warnings.append(message)
                self.deepseek_rotator.rotate()
            except (ValueError, KeyError, IndexError, TypeError):
                message = "DeepSeek : réponse invalide ; essai de la clé ou du fournisseur suivant."
                if message not in self.warnings:
                    self.warnings.append(message)
                self.deepseek_rotator.rotate()
            except Exception as error:
                self._record_failure(
                    "DeepSeek", self._exception_status(error), key_number, self.deepseek_rotator, error=error
                )
        return None

    def _try_gemini(self, file_info: dict, extracted_text: str, is_image: bool) -> dict | None:
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            self.warnings.append("Google Gemini : bibliothèque cliente indisponible.")
            return None

        total_keys = self.gemini_rotator.available_count
        for _ in range(total_keys):
            key = self.gemini_rotator.get_current()
            if not key:
                break
            key_number = self.gemini_rotator.current_idx + 1
            try:
                client = genai.Client(api_key=key)
                prompt = self._build_prompt(file_info, extracted_text, is_image)

                contents = []
                if is_image:
                    image_path = file_info.get("abs_path")
                    # Chargement de l'image
                    with open(image_path, "rb") as f:
                        img_bytes = f.read()
                    mime_type = file_info.get("mime", "image/jpeg")
                    contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))

                contents.append(prompt)

                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json"
                    )
                )

                if response and response.text:
                    parsed = clean_json_response(response.text)
                    return parsed
            except Exception as error:
                self._record_failure("Google Gemini", self._exception_status(error), key_number, self.gemini_rotator, error=error)
        return None

    def _try_groq(self, file_info: dict, extracted_text: str, is_image: bool) -> dict | None:
        try:
            from groq import Groq
        except ImportError:
            self.warnings.append("Groq : bibliothèque cliente indisponible.")
            return None

        total_keys = self.groq_rotator.available_count
        for _ in range(total_keys):
            key = self.groq_rotator.get_current()
            if not key:
                break
            key_number = self.groq_rotator.current_idx + 1
            try:
                client = Groq(api_key=key)
                prompt = self._build_prompt(file_info, extracted_text, is_image)

                messages = []
                if is_image:
                    model = GROQ_VISION_MODEL
                    b64 = encode_image_base64(file_info.get("abs_path"))
                    messages.append({
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                            }
                        ]
                    })
                else:
                    model = GROQ_TEXT_MODEL
                    messages.append({"role": "user", "content": prompt})

                chat_completion = client.chat.completions.create(
                    messages=messages,
                    model=model,
                    temperature=0.2,
                    response_format={"type": "json_object"}
                )
                raw = chat_completion.choices[0].message.content
                if raw:
                    return clean_json_response(raw)
            except Exception as error:
                self._record_failure("Groq", self._exception_status(error), key_number, self.groq_rotator, error=error)
        return None

    def _try_openrouter(self, file_info: dict, extracted_text: str, is_image: bool) -> dict | None:
        total_keys = self.openrouter_rotator.available_count
        for _ in range(total_keys):
            key = self.openrouter_rotator.get_current()
            if not key:
                break
            key_number = self.openrouter_rotator.current_idx + 1
            try:
                prompt = self._build_prompt(file_info, extracted_text, is_image)
                content_payload = []
                if is_image:
                    b64 = encode_image_base64(file_info.get("abs_path"))
                    content_payload = [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                    ]
                else:
                    content_payload = prompt

                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                }
                body = {
                    "model": OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": content_payload}],
                    "temperature": 0.2
                }
                resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=body, timeout=30)
                if resp.status_code == 200:
                    raw = resp.json()["choices"][0]["message"]["content"]
                    return clean_json_response(raw)
                else:
                    self._record_failure("OpenRouter", resp.status_code, key_number, self.openrouter_rotator)
            except Exception as error:
                self._record_failure("OpenRouter", self._exception_status(error), key_number, self.openrouter_rotator, error=error)
        return None

    def _heuristic_fallback(self, file_info: dict, extracted_text: str) -> dict:
        """Mode de repli local intelligent si aucune clé IA n'est disponible ou joignable."""
        family = file_info.get("family")
        stem = file_info.get("stem", "")
        clean_stem = re.sub(r'[\s_\-\.]+', ' ', stem).capitalize()
        
        # Mots-clés et détection basés sur le texte extrait
        lower_txt = extracted_text.lower()
        if any(w in lower_txt for w in ['facture', 'invoice', 'tva', 'montant ttc', 'total ttc', 'iban']):
            category = "Factures & Reçus"
            keywords = ["Facture", "Paiement"]
            clean_name = "Facture_Document"
            summary = "Document identifié comme une facture ou un reçu financier d'après le texte extrait."
        elif any(w in lower_txt for w in ['contrat', 'accord', 'bail', 'signature', 'article']):
            category = "Contrats & Juridique"
            keywords = ["Contrat", "Juridique"]
            clean_name = "Contrat_Document"
            summary = "Document à caractère contractuel ou juridique."
        elif family == 'image':
            category = "Photographies & Paysages"
            keywords = []
            clean_name = "Photo_Image"
            summary = f"Fichier image {file_info.get('filename')} ({file_info.get('size_formatted')})."
        elif family == 'pdf':
            category = "Documents & Rapports"
            keywords = []
            clean_name = "Document_PDF"
            summary = "Analyse IA indisponible : aucun mot-clé fiable n'a été généré pour ce PDF."
        elif family == 'document':
            category = "Documents & Rapports"
            keywords = []
            clean_name = "Fichier_Bureautique"
            summary = "Analyse IA indisponible : aucun mot-clé fiable n'a été généré pour ce document."
        else:
            category = "Divers"
            keywords = [clean_stem[:15] or "Fichier"]
            clean_name = "Fichier_Divers"
            summary = f"Fichier {file_info.get('filename')} de type {family}."

        if family in {"pdf", "image", "document"}:
            keywords = []

        key_entities = [w for w in keywords if w]
        if clean_stem and clean_stem not in key_entities:
            key_entities.append(clean_stem)

        return {
            "category": category,
            "keywords": keywords,
            "key_entities": key_entities,
            "summary": summary,
            "clean_name": clean_name,
            "provider_used": "Heuristique Locale (Sans IA)"
        }
