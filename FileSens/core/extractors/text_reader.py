from pathlib import Path

def read_text_file(file_path: Path | str, max_chars: int = 15000) -> str:
    """Lit un fichier texte ou code avec détection sécurisée de l'encodage."""
    p = Path(file_path)
    encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']
    for enc in encodings:
        try:
            with open(p, 'r', encoding=enc) as f:
                content = f.read(max_chars)
                return content.strip()
        except UnicodeDecodeError:
            continue
        except Exception as e:
            return f"[Erreur de lecture texte : {e}]"
    return "[Erreur : encodage non reconnu]"
