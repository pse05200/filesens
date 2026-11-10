import re
from pathlib import Path

NON_EXPLICIT_PATTERNS = [
    r'^(?:img|dsc|dscf|pano|dcim|pic|photo|wp|vid|mvimg)[_\-\d]',
    r'^(?:scan|scanner|doc|document|page|capture|screenshot|screen)[_\-\d]',
    r'^(?:sans[-_ ]?titre|untitled|nouveau[-_ ]?document|temp|tmp|fichier|file)[_\-\d]*',
    r'^[0-9a-f]{8,}(?:[_\-][0-9a-f]+)*$',  # Hachage md5/sha ou uuid
    r'^\d+$',  # Uniquement des chiffres
    r'.*\(\d+\)$'  # Fichiers avec doublon genre document (1)
]

def is_filename_non_explicit(stem: str) -> bool:
    """Détecte si un nom de fichier est générique ou non explicite."""
    s = stem.strip().lower()
    if len(s) <= 3:
        return True
    for pat in NON_EXPLICIT_PATTERNS:
        if re.match(pat, s, re.IGNORECASE):
            return True
    return False

def sanitize_name_part(text: str) -> str:
    """Nettoie une chaîne pour en faire une partie de nom de fichier valide et propre."""
    # Remplacement des caractères accentués courants
    accents = {
        'à': 'a', 'â': 'a', 'ä': 'a', 'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'î': 'i', 'ï': 'i', 'ô': 'o', 'ö': 'o', 'ù': 'u', 'û': 'u', 'ü': 'u',
        'ç': 'c', 'ñ': 'n'
    }
    t = text.lower()
    for acc, repl in accents.items():
        t = t.replace(acc, repl)
    # Ne conserver que lettres, chiffres, tirets et underscores
    t = re.sub(r'[^a-zA-Z0-9_\-\s]', '', t)
    # Transformer les espaces en underscores et capitaliser chaque mot
    words = [w.capitalize() for w in re.split(r'[\s_\-]+', t) if w]
    return "_".join(words) if words else "Fichier"

def generate_safe_filename(
    target_dir: Path,
    clean_keyword: str,
    date_stamp: str,
    extension: str,
    current_stem: str
) -> tuple[str, bool]:
    """
    Génère un nom de fichier simple au format [MotCle]_[Date].[ext]
    avec garantie stricte anti-écrasement.
    Retourne (nouveau_nom, est_renomme).
    """
    ext = extension if extension.startswith('.') else f".{extension}"
    
    # Si le nom actuel est déjà explicite, on peut le conserver
    should_rename = is_filename_non_explicit(current_stem)

    clean_part = sanitize_name_part(clean_keyword)
    # Nettoyage du date_stamp (format standard YYYYMMDD ou YYYYMMDD_HHMM)
    clean_date = re.sub(r'[^0-9_]', '', date_stamp)

    base_new_stem = f"{clean_part}_{clean_date}"
    candidate_name = f"{base_new_stem}{ext}"

    # Vérification anti-écrasement
    counter = 1
    while (target_dir / candidate_name).exists():
        counter += 1
        candidate_name = f"{base_new_stem}_{counter}{ext}"

    return candidate_name, should_rename
