from pathlib import Path
from uuid import uuid4
import threading
from flask import Flask, render_template, request, jsonify, send_file, abort
from core.scanner import scan_directory
from core.classifier import FileClassifier
from core.file_ops import FileOperations
from core.analysis_cache import load_cached_results, needs_content_keyword_refresh, save_cached_results
from core.environment import get_environment_info

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["TRUSTED_HOSTS"] = ["localhost", "127.0.0.1", "attenudose.com", "www.attenudose.com"]
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Événement de demande d'annulation d'analyse
SCAN_CANCEL_EVENT = threading.Event()

# Un espace de fichiers indépendant pour chaque analyse.
ANALYSIS_CACHE = {}

def get_cached_file(scan_id, file_id):
    return ANALYSIS_CACHE.get(scan_id, {}).get(file_id)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/environment")
def environment_api():
    info = dict(get_environment_info())
    from config import (
        FILESENS_MODE, MAIA_API_KEYS, GEMINI_API_KEYS,
        GROQ_API_KEYS, OPENROUTER_API_KEYS, DEEPSEEK_API_KEYS
    )
    info["configured_mode"] = FILESENS_MODE
    info["keys_summary"] = {
        "maia": len(MAIA_API_KEYS),
        "gemini": len(GEMINI_API_KEYS),
        "groq": len(GROQ_API_KEYS),
        "openrouter": len(OPENROUTER_API_KEYS),
        "deepseek": len(DEEPSEEK_API_KEYS),
    }
    return jsonify(info)

@app.route("/api/scan/cancel", methods=["POST"])
def cancel_scan_api():
    SCAN_CANCEL_EVENT.set()
    return jsonify({"status": "cancelled", "message": "Interruption de l'analyse demandée"})

@app.route("/api/scan", methods=["POST"])
def scan_api():
    SCAN_CANCEL_EVENT.clear()
    data = request.get_json() or {}
    dir_path = str(data.get("directory") or "").strip().strip('"\'')
    if not dir_path:
        return jsonify({"error": "Veuillez indiquer le chemin d'un dossier à analyser."}), 400

    recursive = data.get("recursive", True)
    force_rescan = data.get("force_rescan", False)
    mode = data.get("mode", None)

    target_dir = Path(dir_path).resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        return jsonify({"error": f"Le dossier '{target_dir}' n'existe pas ou n'est pas accessible."}), 400

    cache_file = target_dir / ".filesens_cache.json"
    results = []
    is_from_cache = False
    was_cancelled = False

    cached_by_path = {} if force_rescan else load_cached_results(cache_file)
    try:
        files = scan_directory(target_dir, recursive=recursive)
        current_paths = {file_info["abs_path"] for file_info in files}
        persisted_by_path = {
            path: result for path, result in cached_by_path.items() if path in current_paths
        }
        try:
            classifier = FileClassifier(mode=mode) if mode else FileClassifier()
        except TypeError:
            classifier = FileClassifier()
        for file_info in files:
            if SCAN_CANCEL_EVENT.is_set():
                was_cancelled = True
                break
            cached = cached_by_path.get(file_info["abs_path"])
            if cached is not None and not needs_content_keyword_refresh(cached):
                results.append(cached)
                is_from_cache = True
                continue
            result = classifier.process_file(file_info)
            results.append(result)
            persisted_by_path[file_info["abs_path"]] = result
            save_cached_results(cache_file, list(persisted_by_path.values()))
        # Nettoyage des fichiers supprimés du cache si le scan s'est achevé normalement
        if not was_cancelled and (not files or len(cached_by_path) != len(results)):
            save_cached_results(cache_file, results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    scan_id = uuid4().hex
    scan_results = {res["id"]: res for res in results}
    ANALYSIS_CACHE[scan_id] = scan_results
    warnings = list(dict.fromkeys(
        warning for result in results for warning in result.get("ai_warnings", [])
    ))

    return jsonify({
        "status": "cancelled" if was_cancelled else "success",
        "was_cancelled": was_cancelled,
        "scan_id": scan_id,
        "directory": str(target_dir),
        "total_files": len(results),
        "from_cache": is_from_cache,
        "warnings": warnings,
        "files": results
    })

@app.route("/api/preview/<file_id>")
def preview_file(file_id):
    file_info = get_cached_file(request.args.get("scan_id"), file_id)
    if not file_info:
        abort(404)
    file_path = Path(file_info["abs_path"])
    if not file_path.exists():
        abort(404)
    mime = file_info.get("mime")
    if file_path.suffix.lower() == ".pdf":
        mime = "application/pdf"
    return send_file(
        str(file_path),
        mimetype=mime,
        as_attachment=False,
        download_name=file_path.name
    )

@app.route("/api/preview-docx/<file_id>")
def preview_docx_api(file_id):
    import html
    file_info = get_cached_file(request.args.get("scan_id"), file_id)
    if not file_info:
        abort(404)
    file_path = Path(file_info["abs_path"])
    if not file_path.exists():
        abort(404)

    ext = file_path.suffix.lower()
    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(str(file_path))
            html_parts = []
            for p in doc.paragraphs:
                text = p.text.strip()
                if not text:
                    continue
                style_name = (p.style.name if p.style else '').lower()
                safe_text = html.escape(text)
                if 'heading 1' in style_name or 'titre 1' in style_name:
                    html_parts.append(f'<h1 class="text-xl font-bold text-slate-900 dark:text-white border-b border-slate-200 dark:border-slate-700 pb-2 mt-4 mb-2">{safe_text}</h1>')
                elif 'heading 2' in style_name or 'titre 2' in style_name:
                    html_parts.append(f'<h2 class="text-lg font-semibold text-slate-800 dark:text-slate-100 mt-3 mb-1.5">{safe_text}</h2>')
                elif 'heading 3' in style_name or 'titre 3' in style_name:
                    html_parts.append(f'<h3 class="text-base font-semibold text-slate-700 dark:text-slate-200 mt-2 mb-1">{safe_text}</h3>')
                else:
                    html_parts.append(f'<p class="text-sm leading-relaxed text-slate-700 dark:text-slate-300 mb-2">{safe_text}</p>')

            for table in doc.tables:
                html_parts.append('<div class="overflow-x-auto my-3 rounded-lg border border-slate-200 dark:border-slate-700"><table class="min-w-full border-collapse text-xs bg-white dark:bg-slate-800">')
                for row_idx, row in enumerate(table.rows):
                    row_bg = 'bg-slate-100 dark:bg-slate-700 font-semibold text-slate-900 dark:text-white' if row_idx == 0 else 'hover:bg-slate-50 dark:hover:bg-slate-700/50 text-slate-700 dark:text-slate-300'
                    html_parts.append(f'<tr class="border-b border-slate-200 dark:border-slate-700 {row_bg}">')
                    for cell in row.cells:
                        tag = 'th' if row_idx == 0 else 'td'
                        html_parts.append(f'<{tag} class="border border-slate-200 dark:border-slate-700 px-3 py-2 text-left">{html.escape(cell.text.strip())}</{tag}>')
                    html_parts.append('</tr>')
                html_parts.append('</table></div>')

            if html_parts:
                return jsonify({"status": "success", "type": "docx", "html": "".join(html_parts)})
        except Exception as e:
            return jsonify({"status": "error", "message": f"Erreur lecture docx: {e}"}), 500

    # Repli pour autres formats texte ou documents
    preview_txt = file_info.get("extracted_text_preview") or ""
    safe_txt = html.escape(preview_txt)
    return jsonify({
        "status": "success",
        "type": "text",
        "html": f'<div class="p-3 text-xs font-mono text-slate-700 dark:text-slate-300 whitespace-pre-wrap">{safe_txt}</div>'
    })

@app.route("/api/update-tags", methods=["POST"])
def update_tags_api():
    data = request.get_json() or {}
    scan_id = data.get("scan_id")
    file_id = data.get("file_id")
    raw_keywords = data.get("keywords", [])

    if not isinstance(raw_keywords, list):
        return jsonify({"error": "Format de mots-clés invalide"}), 400

    clean_keywords = []
    for k in raw_keywords:
        item = str(k).strip().lstrip('#').strip()
        if item and item not in clean_keywords:
            clean_keywords.append(item)

    file_info = get_cached_file(scan_id, file_id)
    if not file_info:
        return jsonify({"error": "Fichier introuvable en mémoire"}), 404

    file_info["keywords"] = clean_keywords

    # Sauvegarde sur disque dans .filesens_cache.json si disponible
    try:
        abs_path = file_info.get("abs_path")
        if abs_path:
            file_p = Path(abs_path)
            for parent_candidate in [file_p.parent, file_p.parent.parent]:
                cache_file = parent_candidate / ".filesens_cache.json"
                if cache_file.exists():
                    cached = load_cached_results(cache_file)
                    if abs_path in cached:
                        cached[abs_path]["keywords"] = clean_keywords
                        save_cached_results(cache_file, list(cached.values()))
                        break
    except Exception as e:
        app.logger.warning(f"Erreur mise à jour cache disque pour tags: {e}")

    return jsonify({"status": "success", "keywords": clean_keywords})

@app.route("/api/rename", methods=["POST"])
def rename_api():
    data = request.get_json() or {}
    file_id = data.get("file_id")
    new_name = data.get("new_filename")

    file_info = get_cached_file(data.get("scan_id"), file_id)
    if not file_info:
        return jsonify({"error": "Fichier introuvable en mémoire"}), 404

    try:
        file_ops = FileOperations(Path(file_info["abs_path"]).parent)
        new_path = file_ops.rename_file(file_info["abs_path"], new_name)
        # Met à jour les infos dans le cache
        file_info["abs_path"] = str(new_path)
        file_info["filename"] = new_path.name
        file_info["stem"] = new_path.stem
        file_info["is_non_explicit_name"] = False
        return jsonify({"status": "success", "new_filename": new_path.name, "abs_path": str(new_path)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/move", methods=["POST"])
def move_api():
    data = request.get_json() or {}
    file_id = data.get("file_id")
    subfolder = data.get("subfolder")

    file_info = get_cached_file(data.get("scan_id"), file_id)
    if not file_info:
        return jsonify({"error": "Fichier introuvable"}), 404

    try:
        base_dir = Path(file_info["abs_path"]).parent
        file_ops = FileOperations(base_dir)
        new_path = file_ops.move_file(file_info["abs_path"], subfolder)
        file_info["abs_path"] = str(new_path)
        return jsonify({"status": "success", "new_path": str(new_path)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/trash", methods=["POST"])
def trash_api():
    data = request.get_json() or {}
    file_id = data.get("file_id")

    file_info = get_cached_file(data.get("scan_id"), file_id)
    if not file_info:
        return jsonify({"error": "Fichier introuvable"}), 404

    try:
        base_dir = Path(file_info["abs_path"]).parent
        file_ops = FileOperations(base_dir)
        new_path = file_ops.trash_file(file_info["abs_path"])
        file_info["abs_path"] = str(new_path)
        file_info["is_trashed"] = True
        return jsonify({"status": "success", "trash_path": str(new_path)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/delete", methods=["POST"])
def delete_api():
    data = request.get_json() or {}
    file_id = data.get("file_id")

    file_info = get_cached_file(data.get("scan_id"), file_id)
    if not file_info:
        return jsonify({"error": "Fichier introuvable"}), 404

    try:
        file_ops = FileOperations(Path(file_info["abs_path"]).parent)
        file_ops.permanent_delete(file_info["abs_path"])
        del ANALYSIS_CACHE[data["scan_id"]][file_id]
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/batch", methods=["POST"])
def batch_api():
    data = request.get_json() or {}
    action = data.get("action")  # 'rename_suggested', 'move_category', 'trash'
    file_ids = data.get("file_ids", [])
    scan_results = ANALYSIS_CACHE.get(data.get("scan_id"))
    if scan_results is None:
        return jsonify({"error": "Analyse introuvable en mémoire"}), 404
    
    results = []
    for fid in file_ids:
        file_info = scan_results.get(fid)
        if not file_info:
            continue
        try:
            file_ops = FileOperations(Path(file_info["abs_path"]).parent)
            if action == 'rename_suggested' and file_info.get("suggested_filename"):
                new_p = file_ops.rename_file(file_info["abs_path"], file_info["suggested_filename"])
                file_info["abs_path"] = str(new_p)
                file_info["filename"] = new_p.name
                file_info["is_non_explicit_name"] = False
                results.append({"id": fid, "status": "renamed", "filename": new_p.name})
            elif action == 'move_category':
                custom_subfolder = data.get("subfolder")
                if custom_subfolder and str(custom_subfolder).strip():
                    cat_folder = str(custom_subfolder).replace("/", "_").strip()
                else:
                    cat_folder = file_info.get("category", "Divers").replace("/", "_").strip()
                new_p = file_ops.move_file(file_info["abs_path"], cat_folder)
                file_info["abs_path"] = str(new_p)
                file_info["parent_dir"] = new_p.parent.name
                results.append({"id": fid, "status": "moved", "path": str(new_p), "parent_dir": new_p.parent.name})
            elif action == 'trash':
                new_p = file_ops.trash_file(file_info["abs_path"])
                file_info["abs_path"] = str(new_p)
                file_info["is_trashed"] = True
                results.append({"id": fid, "status": "trashed"})
        except Exception as e:
            results.append({"id": fid, "error": str(e)})

    # Mise à jour du cache sur disque si applicable
    try:
        for fid in file_ids:
            item = scan_results.get(fid)
            if item and item.get("abs_path"):
                p = Path(item["abs_path"])
                for cand in [p.parent, p.parent.parent]:
                    cache_cand = cand / ".filesens_cache.json"
                    if cache_cand.exists():
                        cached_data = load_cached_results(cache_cand)
                        cached_data[item["abs_path"]] = item
                        save_cached_results(cache_cand, list(cached_data.values()))
                        break
    except Exception as e:
        app.logger.warning(f"Erreur mise à jour cache disque batch: {e}")

    return jsonify({"status": "success", "processed": results})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7885, debug=False)
