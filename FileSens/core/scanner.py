import os
import mimetypes
import hashlib
from datetime import datetime
from pathlib import Path
from PIL import Image, ExifTags

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif', '.gif'}
DOC_EXTENSIONS = {
    '.pdf', '.docx', '.doc', '.dot', '.dotx',
    '.odt', '.ott', '.ods', '.ots', '.odp', '.otp',
    '.xlsx', '.xls', '.pptx', '.ppt', '.csv', '.rtf'
}
TEXT_EXTENSIONS = {'.txt', '.md', '.rst', '.log', '.json', '.yaml', '.yml', '.xml', '.html', '.css', '.js', '.ts', '.py', '.sh', '.c', '.cpp', '.h', '.sql'}
ARCHIVE_EXTENSIONS = {'.zip', '.tar', '.gz', '.bz2', '.7z', '.rar'}
AUDIO_VIDEO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.mp4', '.mkv', '.avi', '.mov'}

IGNORE_DIRS = {'.git', '.trash', '__pycache__', '.venv', 'venv', 'node_modules', '.idea', '.vscode'}

def get_file_family(ext: str, mime: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXTENSIONS or (mime and mime.startswith('image/')):
        return 'image'
    if ext == '.pdf':
        return 'pdf'
    if ext in DOC_EXTENSIONS:
        return 'document'
    if ext in TEXT_EXTENSIONS or (mime and mime.startswith('text/')):
        return 'text'
    if ext in ARCHIVE_EXTENSIONS:
        return 'archive'
    if ext in AUDIO_VIDEO_EXTENSIONS or (mime and (mime.startswith('audio/') or mime.startswith('video/'))):
        return 'media'
    return 'other'

def extract_exif_date(file_path: Path) -> datetime | None:
    """Tente d'extraire la date de prise de vue d'une image via ses tags EXIF."""
    try:
        with Image.open(file_path) as img:
            exif = img.getexif()
            if not exif:
                return None
            for tag_id, value in exif.items():
                tag_name = ExifTags.TAGS.get(tag_id, tag_id)
                if tag_name in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
                    # Format standard EXIF : 'YYYY:MM:DD HH:MM:SS'
                    try:
                        clean_val = str(value).strip()
                        return datetime.strptime(clean_val, '%Y:%m:%d %H:%M:%S')
                    except Exception:
                        pass
    except Exception:
        pass
    return None

def format_file_size(size_bytes: int) -> str:
    for unit in ['o', 'Ko', 'Mo', 'Go', 'To']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} Po"

def scan_directory(directory_path: str | Path, recursive: bool = True) -> list[dict]:
    """Scanne le répertoire cible et extrait les métadonnées de base de chaque fichier."""
    target_dir = Path(directory_path).resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        raise FileNotFoundError(f"Le dossier n'existe pas : {directory_path}")

    files_info = []

    pattern = target_dir.rglob('*') if recursive else target_dir.glob('*')

    for p in sorted(pattern):
        # Ignore les dossiers, liens brisés ou répertoires cachés
        if not p.is_file():
            continue
        if p.name.startswith('.filesens'):
            continue
        if any(ignored in p.parts for ignored in IGNORE_DIRS):
            continue

        rel_path = p.relative_to(target_dir)
        ext = p.suffix.lower()
        mime, _ = mimetypes.guess_type(str(p))
        stat = p.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime)

        # Date EXIF si image
        exif_dt = extract_exif_date(p) if ext in IMAGE_EXTENSIONS else None
        effective_date = exif_dt or mtime

        # Inclure la racine du scan pour distinguer les mêmes chemins relatifs
        # présents dans des dossiers différents.
        file_id = hashlib.sha256(str(p).encode('utf-8')).hexdigest()[:24]

        family = get_file_family(ext, mime or '')
        parent_dir = str(rel_path.parent).replace('\\', '/') if str(rel_path.parent) != '.' else ""

        files_info.append({
            "id": file_id,
            "filename": p.name,
            "stem": p.stem,
            "ext": ext,
            "rel_path": str(rel_path).replace('\\', '/'),
            "parent_dir": parent_dir,
            "abs_path": str(p),
            "size_bytes": stat.st_size,
            "size_formatted": format_file_size(stat.st_size),
            "mtime": mtime.isoformat(),
            "date_display": effective_date.strftime("%Y-%m-%d %H:%M"),
            "date_stamp": effective_date.strftime("%Y%m%d_%H%M%S"),
            "date_day": effective_date.strftime("%Y%m%d"),
            "family": family,
            "mime": mime or 'application/octet-stream'
        })

    return files_info
