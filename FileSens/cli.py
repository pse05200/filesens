import argparse
import sys
import json
import csv
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt, Confirm

# Configuration robuste de l'encodage pour la console Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import colorama
    colorama.just_fix_windows_console()
except Exception:
    pass

from core.scanner import scan_directory
from core.classifier import FileClassifier
from core.file_ops import FileOperations
from core.analysis_cache import load_cached_results, needs_content_keyword_refresh, save_cached_results
from core.environment import get_environment_info

console = Console()

def run_cli():
    parser = argparse.ArgumentParser(
        description="Analyseur intelligent de fichiers avec OCR, Vision IA, renommage avec date et tri."
    )
    parser.add_argument("directory", nargs="?", default=".", help="Répertoire à analyser (par défaut: dossier courant)")
    parser.add_argument("--no-recursive", action="store_true", help="Ne pas scanner récursivement les sous-dossiers")
    parser.add_argument("--force", "-f", action="store_true", help="Forcer une nouvelle analyse même si un cache existe")
    parser.add_argument("--rename-auto", action="store_true", help="Renommer automatiquement les fichiers avec nom non-explicite")
    parser.add_argument("--organize", action="store_true", help="Déplacer les fichiers dans des sous-dossiers par catégorie")
    parser.add_argument("--interactive", "-i", action="store_true", help="Mode interactif au clavier pour chaque fichier")
    parser.add_argument("--export-json", type=str, help="Fichier d'export JSON des résultats")
    parser.add_argument("--export-csv", type=str, help="Fichier d'export CSV des résultats")
    parser.add_argument(
        "--mode",
        choices=["auto", "maia", "external"],
        default=None,
        help="Mode IA : 'auto' (Maïa au CEA, APIs externes sur VPS), 'maia' (Maïa uniquement), 'external' (APIs externes multi-clés .env uniquement)"
    )

    args = parser.parse_args()
    target_dir = Path(str(args.directory).strip().strip('"\'')).resolve()

    if not target_dir.exists() or not target_dir.is_dir():
        console.print(f"[bold red]Erreur : Le répertoire {target_dir} n'existe pas.[/bold red]")
        sys.exit(1)

    console.print(f"\n[bold cyan]🔍 Scan du répertoire :[/bold cyan] [underline]{target_dir}[/underline]\n")
    
    from config import FILESENS_MODE
    active_mode = (args.mode or FILESENS_MODE or "auto").lower()
    env = get_environment_info()
    if active_mode == "external":
        console.print("[bold yellow]🌐 Mode IA sélectionné : APIs Externes (Multi-clés .env)[/bold yellow]")
        console.print("[dim]  Maïa Chat désactivé. Cascade : Google Gemini, Groq, OpenRouter, DeepSeek.[/dim]\n")
    elif active_mode == "maia":
        console.print("[bold green]🏛️ Mode IA sélectionné : Maïa Chat CEA exclusif[/bold green]\n")
    elif env.get("is_cea"):
        console.print(f"[bold green]✓ Environnement détecté : Réseau CEA ({env.get('fqdn') or env.get('hostname')})[/bold green]")
        console.print("[dim]  Mode Auto : Maïa Chat CEA en priorité avec secours APIs externes.[/dim]\n")
    else:
        console.print(f"[bold cyan]✓ Environnement détecté : Externe / VPS ({env.get('hostname')})[/bold cyan]")
        console.print("[dim]  Mode Auto : Maïa indisponible hors CEA -> APIs externes multi-clés du .env.[/dim]\n")

    files = scan_directory(target_dir, recursive=not args.no_recursive)
    if not files:
        console.print("[yellow]Aucun fichier éligible trouvé.[/yellow]")
        return

    console.print(f"[green]✓ {len(files)} fichiers détectés.[/green] Lancement de l'analyse...\n")

    try:
        classifier = FileClassifier(mode=args.mode) if args.mode else FileClassifier()
    except TypeError:
        classifier = FileClassifier()
    file_ops = FileOperations(target_dir)
    results = []
    cache_file = target_dir / ".filesens_cache.json"

    cached_by_path = {} if args.force else load_cached_results(cache_file)
    current_paths = {file_info["abs_path"] for file_info in files}
    persisted_by_path = {
        path: result for path, result in cached_by_path.items() if path in current_paths
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("[cyan]Analyse et OCR/Vision...", total=len(files))
        for f in files:
            # Si déjà en cache et fichier non modifié
            if f["abs_path"] in cached_by_path and not needs_content_keyword_refresh(cached_by_path[f["abs_path"]]):
                progress.update(task, description=f"[dim]Depuis le cache : {f['filename'][:30]}")
                results.append(cached_by_path[f["abs_path"]])
            else:
                progress.update(task, description=f"[cyan]Traitement de {f['filename'][:30]}")
                res = classifier.process_file(f)
                results.append(res)
                persisted_by_path[f["abs_path"]] = res
                save_cached_results(cache_file, list(persisted_by_path.values()))
            progress.advance(task)

    # Sauvegarde automatique du cache pour l'interface web
    try:
        save_cached_results(cache_file, results)
        console.print(f"\n[green]💾 Résultats sauvegardés dans :[/green] [bold]{cache_file.name}[/bold] (utilisable directement par l'interface web)")
    except Exception as e:
        console.print(f"[yellow]Avertissement : impossible de sauvegarder le cache ({e})[/yellow]")

    # Affichage du tableau récapitulatif
    warnings = list(dict.fromkeys(
        warning for result in results for warning in result.get("ai_warnings", [])
    ))
    for warning in warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    table = Table(title="📊 Synthèse de l'Analyse des Fichiers", show_header=True, header_style="bold magenta")
    table.add_column("Fichier d'Origine", style="dim", max_width=25)
    table.add_column("Catégorie", style="bold green")
    table.add_column("Mots-Clés", style="yellow")
    table.add_column("Nouveau Nom Suggéré (avec Date)", style="cyan")
    table.add_column("Résumé", max_width=45)

    for r in results:
        keywords_str = ", ".join(r.get("keywords", []))
        table.add_row(
            r["filename"],
            r["category"],
            keywords_str,
            r["suggested_filename"],
            r["summary"]
        )

    console.print(table)

    # Actions automatiques ou interactives
    if args.rename_auto:
        console.print("\n[bold yellow]⚡ Renommage automatique des fichiers non-explicites...[/bold yellow]")
        for r in results:
            if r["is_non_explicit_name"]:
                old_p = r["abs_path"]
                new_p = file_ops.rename_file(old_p, r["suggested_filename"])
                console.print(f"[green]✓ Renommé :[/green] {r['filename']} ➜ [bold]{new_p.name}[/bold]")

    if args.organize:
        console.print("\n[bold yellow]📁 Organisation des fichiers par catégorie...[/bold yellow]")
        for r in results:
            cat_folder = r["category"].replace("/", "_").replace("&", "_").strip()
            moved_p = file_ops.move_file(r["abs_path"], cat_folder)
            console.print(f"[blue]➜ Déplacé dans {cat_folder}/ :[/blue] {moved_p.name}")

    if args.interactive:
        console.print("\n[bold cyan]🎮 Mode Triage Interactif :[/bold cyan]")
        for r in results:
            console.print(f"\n[bold]--- Fichier : {r['filename']} ---[/bold]")
            console.print(f"[yellow]Mots-clés :[/yellow] {', '.join(r['keywords'])}")
            console.print(f"[green]Catégorie :[/green] {r['category']}")
            console.print(f"[cyan]Nouveau nom proposé :[/cyan] {r['suggested_filename']}")
            console.print(f"[white]Résumé :[/white] {r['summary']}")

            action = Prompt.ask(
                "Action à effectuer",
                choices=["r", "m", "s", "d", "passer"],
                default="passer"
            )
            # r: renommer, m: déplacer, s: supprimer (corbeille), d: supprimer définitif
            if action == "r":
                new_p = file_ops.rename_file(r["abs_path"], r["suggested_filename"])
                console.print(f"[green]Renommé en {new_p.name}[/green]")
            elif action == "m":
                cat_folder = r["category"].replace("/", "_").strip()
                moved_p = file_ops.move_file(r["abs_path"], cat_folder)
                console.print(f"[blue]Déplacé dans {cat_folder}/[/blue]")
            elif action == "s":
                file_ops.trash_file(r["abs_path"])
                console.print("[yellow]Fichier placé à la corbeille .trash[/yellow]")
            elif action == "d":
                if Confirm.ask("Confirmer la suppression définitive ?"):
                    file_ops.permanent_delete(r["abs_path"])
                    console.print("[red]Fichier définitivement supprimé.[/red]")

    # Export des résultats
    if args.export_json:
        with open(args.export_json, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        console.print(f"[green]Rapport JSON exporté : {args.export_json}[/green]")

    if args.export_csv:
        with open(args.export_csv, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Fichier", "Catégorie", "Mots-Clés", "Nouveau Nom", "Résumé", "Taille", "OCR"])
            for r in results:
                writer.writerow([
                    r["filename"],
                    r["category"],
                    ", ".join(r["keywords"]),
                    r["suggested_filename"],
                    r["summary"],
                    r["size_formatted"],
                    "Oui" if r.get("ocr_used") else "Non"
                ])
        console.print(f"[green]Rapport CSV exporté : {args.export_csv}[/green]")

if __name__ == "__main__":
    run_cli()
