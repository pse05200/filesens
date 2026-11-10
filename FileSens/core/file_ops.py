import shutil
import json
from pathlib import Path
from datetime import datetime
from config import TRASH_DIR_NAME

class FileOperations:
    def __init__(self, base_directory: Path | str):
        self.base_dir = Path(base_directory).resolve()
        self.trash_dir = self.base_dir / TRASH_DIR_NAME
        self.history_file = self.trash_dir / "operations_history.json"

    def _ensure_trash(self):
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    def _log_action(self, action_type: str, src_path: str, dest_path: str = ""):
        self._ensure_trash()
        history = []
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except Exception:
                history = []

        history.append({
            "timestamp": datetime.now().isoformat(),
            "action": action_type,
            "src": str(src_path),
            "dest": str(dest_path)
        })
        with open(self.history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2)

    def rename_file(self, file_path: Path | str, new_filename: str) -> Path:
        """Renomme un fichier dans son dossier avec protection anti-écrasement."""
        p = Path(file_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Le fichier n'existe pas : {p}")

        target = p.parent / new_filename
        if target == p:
            return p

        if target.exists():
            stem = target.stem
            ext = target.suffix
            counter = 2
            while (p.parent / f"{stem}_{counter}{ext}").exists():
                counter += 1
            target = p.parent / f"{stem}_{counter}{ext}"

        p.rename(target)
        self._log_action("rename", str(p), str(target))
        return target

    def move_file(self, file_path: Path | str, target_subfolder: str) -> Path:
        """Déplace un fichier vers un sous-dossier (ex: une catégorie)."""
        p = Path(file_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Le fichier n'existe pas : {p}")

        dest_dir = self.base_dir / target_subfolder
        dest_dir.mkdir(parents=True, exist_ok=True)

        target = dest_dir / p.name
        if target.exists():
            stem = p.stem
            ext = p.suffix
            counter = 2
            while (dest_dir / f"{stem}_{counter}{ext}").exists():
                counter += 1
            target = dest_dir / f"{stem}_{counter}{ext}"

        shutil.move(str(p), str(target))
        self._log_action("move", str(p), str(target))
        return target

    def trash_file(self, file_path: Path | str) -> Path:
        """Déplace un fichier vers le dossier de corbeille sécurisée."""
        p = Path(file_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Le fichier n'existe pas : {p}")

        self._ensure_trash()
        target = self.trash_dir / p.name
        if target.exists():
            stem = p.stem
            ext = p.suffix
            counter = 2
            while (self.trash_dir / f"{stem}_{counter}{ext}").exists():
                counter += 1
            target = self.trash_dir / f"{stem}_{counter}{ext}"

        shutil.move(str(p), str(target))
        self._log_action("trash", str(p), str(target))
        return target

    def permanent_delete(self, file_path: Path | str) -> bool:
        """Supprime définitivement un fichier."""
        p = Path(file_path).resolve()
        if not p.exists():
            return False
        p.unlink()
        self._log_action("delete", str(p), "")
        return True
