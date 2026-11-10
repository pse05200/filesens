import os
import re
import hashlib
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

# Activation de truststore pour les certificats SSL Windows (indispensable sur réseau CEA)
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

# Charge les variables d'environnement depuis .env s'il existe
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

def get_api_keys(env_var_name: str, fallback_var: str = None) -> list[str]:
    """Extrait une liste de clés API séparées par des virgules ou points-virgules."""
    val = os.getenv(env_var_name, "")
    if not val and fallback_var:
        val = os.getenv(fallback_var, "")
    if not val:
        return []
    keys = [k.strip() for k in re.split(r'[,;\n]', val) if k.strip()]
    return keys

def get_maia_api_keys() -> list[str]:
    """
    Récupère les clés de l'API Maïa CEA.
    Vérifie d'abord MAIA_API_KEYS / MAIA_API_KEY dans l'environnement / .env.
    Si non renseigné, tente de récupérer la clé enregistrée dans IRIS (dossier parent).
    """
    keys = get_api_keys("MAIA_API_KEYS", "MAIA_API_KEY")
    if keys:
        return keys
    
    # Rétrocompatibilité / intégration directe avec IRIS CEA
    import importlib.util
    for candidate in [BASE_DIR.parent, BASE_DIR.parent.parent]:
        iris_config_path = candidate / "config.py"
        iris_policy_path = candidate / "network_policy.py"
        if iris_config_path.exists() and iris_policy_path.exists():
            try:
                candidate_str = str(candidate.resolve())
                if candidate_str not in sys.path:
                    sys.path.insert(0, candidate_str)
                spec = importlib.util.spec_from_file_location("iris_config_module", iris_config_path)
                iris_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(iris_module)
                iris_cfg = iris_module.load_config()
                if iris_cfg and getattr(iris_cfg, "api_key", ""):
                    return [iris_cfg.api_key]
            except Exception:
                pass
    return []

# Mode de fonctionnement IA :
# 'auto'     : Détecte automatiquement (Maïa sur réseau CEA, APIs externes sur VPS/hors CEA)
# 'maia'     : Force l'utilisation exclusive de Maïa Chat CEA (Mistral)
# 'external' : Force l'utilisation des APIs externes (Gemini, Groq, OpenRouter, DeepSeek), jamais Maïa
FILESENS_MODE = os.getenv("FILESENS_MODE", "auto").strip().lower()

# Clés d'API avec support multi-clés pour rotation / bascule
MAIA_API_KEYS = get_maia_api_keys()
GEMINI_API_KEYS = get_api_keys("GEMINI_API_KEYS", "GEMINI_API_KEY")
GROQ_API_KEYS = get_api_keys("GROQ_API_KEYS", "GROQ_API_KEY")
OPENROUTER_API_KEYS = get_api_keys("OPENROUTER_API_KEYS", "OPENROUTER_API_KEY")
DEEPSEEK_API_KEYS = get_api_keys("DEEPSEEK_API_KEYS", "DEEPSEEK_API_KEY")

# Modèles IA par fournisseur
# Maïa CEA (Mistral) : Mistral Small pour texte et images
MAIA_BASE_URL = os.getenv("MAIA_BASE_URL", "https://maia-chat.intra.cea.fr/openai/")
MAIA_TEXT_MODEL = os.getenv("MAIA_TEXT_MODEL", "mistralai-small")
MAIA_VISION_MODEL = os.getenv("MAIA_VISION_MODEL", "mistralai-small")
MAIA_TIMEOUT = int(os.getenv("MAIA_TIMEOUT", "60"))

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "llama-3.2-11b-vision-preview")
GROQ_TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# Empreinte des paramètres IA : permet de réessayer un ancien repli local
# après ajout ou remplacement d'une clé, sans conserver les clés dans le cache.
AI_CONFIG_SIGNATURE = hashlib.sha256(json.dumps({
    "mode": FILESENS_MODE,
    "keys": [MAIA_API_KEYS, DEEPSEEK_API_KEYS, GEMINI_API_KEYS, GROQ_API_KEYS, OPENROUTER_API_KEYS],
    "models": [MAIA_TEXT_MODEL, MAIA_VISION_MODEL, DEEPSEEK_MODEL, GEMINI_MODEL, GROQ_VISION_MODEL, GROQ_TEXT_MODEL, OPENROUTER_MODEL],
}, sort_keys=True).encode("utf-8")).hexdigest()[:16]

# Configuration OCR
OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "fra+eng")
MIN_PDF_TEXT_LENGTH = int(os.getenv("MIN_PDF_TEXT_LENGTH", "60"))

# Paramètres de tri et organisation
TRASH_DIR_NAME = ".trash"

# Catégories standardisées
DEFAULT_CATEGORIES = [
    "Factures & Reçus",
    "Contrats & Juridique",
    "Banque & Finances",
    "Administratif & Identité",
    "Photographies & Paysages",
    "Graphiques & Schémas",
    "Documents & Rapports",
    "Tableurs & Données",
    "Code source & Tech",
    "Divers"
]
