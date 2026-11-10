import json
import os
import tempfile
from pathlib import Path
from config import AI_CONFIG_SIGNATURE


def load_cached_results(cache_file: Path) -> dict[str, dict]:
    try:
        with cache_file.open("r", encoding="utf-8") as cache:
            data = json.load(cache)
        if not isinstance(data, list):
            return {}
        return {
            item["abs_path"]: item
            for item in data
            if isinstance(item, dict) and isinstance(item.get("abs_path"), str)
        }
    except (OSError, ValueError):
        return {}


def needs_content_keyword_refresh(result: dict) -> bool:
    if (
        result.get("provider_used") == "Heuristique Locale (Sans IA)"
        and result.get("ai_config_signature") != AI_CONFIG_SIGNATURE
    ):
        return True
    if result.get("family") not in {"pdf", "image", "document"}:
        return False
    keywords = result.get("keywords", [])
    if not isinstance(keywords, list):
        return True
    if not keywords:
        return result.get("provider_used") != "Heuristique Locale (Sans IA)"
    if len(keywords) != 2 or result.get("provider_used") == "Heuristique Locale (Sans IA)":
        return True
    return any(
        not isinstance(word, str) or word.strip().casefold() in {
            "pdf", "document", "document pdf", "fichier", "photo", "image", "photographie",
            "bureautique", "fichier bureautique", "word", "document word", "powerpoint",
            "présentation", "presentation", "tableur", "classeur excel"
        }
        for word in keywords
    )


def save_cached_results(cache_file: Path, results: list[dict]) -> None:
    # Replace the file atomically so an interruption cannot leave truncated JSON.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=cache_file.parent,
            prefix=".filesens_cache_", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(results, temporary, ensure_ascii=False, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, cache_file)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
