#!/usr/bin/env python3
"""
Outil de test et de diagnostic d'environnement CEA et de l'API Maïa Chat CEA.
Vérifie :
1. L'environnement réseau (Adresses IP, domaine CEA intra.cea.fr, résolution Maïa)
2. L'accès HTTPS à l'API Maïa Chat
3. Un appel test Texte avec Mistral Small
4. Un appel test Vision/Image avec Mistral Small
"""

import sys
import time
from pathlib import Path

# Encodage sécurisé pour la console Windows
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

# S'assurer que le dossier racine de FileSens est dans sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Activation de truststore pour le magasin de certificats Windows (technique Plume / IRIS)
try:
    import truststore
    truststore.inject_into_ssl()
except Exception as e:
    print(f"[!] Avertissement truststore: {e}")

from core.environment import get_environment_info, is_cea_network
from core.ai_router import AIRouter
from config import MAIA_BASE_URL, MAIA_TEXT_MODEL, MAIA_VISION_MODEL, MAIA_API_KEYS


def run_diagnostics():
    print("=" * 70)
    print("   DIAGNOSTIC ENVIRONNEMENT & CONNECTIVITÉ MAÏA CEA")
    print("=" * 70)

    # 1. Analyse de l'environnement réseau
    env = get_environment_info(refresh=True)
    print(f"[*] Nom d'hôte      : {env['hostname']}")
    print(f"[*] FQDN            : {env['fqdn']}")
    print(f"[*] Adresses IP     : {', '.join(env['local_ips']) or 'Aucune'}")
    if env["cea_ips"]:
        print(f"[*] IP réseau CEA   : {', '.join(env['cea_ips'])} (Plage interne CEA)")
    print(f"[*] Statut réseau   : {env['environment_label']}")
    print(f"[*] Hôte Maïa       : {env['maia_host']}")
    print(f"[*] Résolution IP   : {env['maia_ip'] or 'Échec de résolution'}")
    print(f"[*] Port 443 joignable : {'OUI' if env['maia_reachable'] else 'NON'}")

    # Clé API Maïa
    has_key = len(MAIA_API_KEYS) > 0
    key_preview = f"{MAIA_API_KEYS[0][:7]}..." if has_key else "Non renseignée"
    print(f"[*] Clé API Maïa    : {key_preview} ({len(MAIA_API_KEYS)} clé(s) disponible(s))")

    print("=" * 70)

    if not env["is_cea"]:
        print("[!] Attention : Vous n'êtes pas détecté sur le réseau CEA ou l'accès VPN est inactif.")
        return

    if not has_key:
        print("[!] Aucune clé API Maïa n'a été trouvée dans .env ni dans IRIS (configuration.json).")
        return

    router = AIRouter()

    # 2. Test Texte avec Mistral Medium
    print(f"\n[>] Test Texte via Maïa ({MAIA_TEXT_MODEL})...")
    dummy_text_file = {
        "filename": "Rapport_Financier_2024.txt",
        "family": "document",
        "ext": ".txt",
        "size_formatted": "1.2 Ko",
        "stem": "Rapport_Financier_2024"
    }
    extracted_sample = (
        "Direction Générale CEA - Bilan d'activité financière du département 2024. "
        "Les investissements en recherche s'élèvent à 14.5 millions d'euros avec un solde excédentaire."
    )

    t0 = time.time()
    res_text = router.analyze_file(dummy_text_file, extracted_text=extracted_sample)
    t_text = time.time() - t0

    print(f"[OK] Réponse reçue en {t_text:.2f}s | Fournisseur: {res_text.get('provider_used')}")
    print(f"    - Catégorie  : {res_text.get('category')}")
    print(f"    - Mots-clés  : {res_text.get('keywords')}")
    print(f"    - Nom suggéré: {res_text.get('clean_name')}")
    print(f"    - Résumé     : {res_text.get('summary')}")

    # 3. Test Vision / Image avec Mistral Small
    demo_image = BASE_DIR / "demo_folder" / "DSC_0099.png"
    if not demo_image.exists():
        demo_image = BASE_DIR / "demo_folder" / "IMG_20240315_1423.png"

    if demo_image.exists():
        print(f"\n[>] Test Vision/Image via Maïa ({MAIA_VISION_MODEL}) sur {demo_image.name}...")
        dummy_image_file = {
            "filename": demo_image.name,
            "family": "image",
            "ext": demo_image.suffix,
            "size_formatted": f"{demo_image.stat().st_size / 1024:.1f} Ko",
            "stem": demo_image.stem,
            "abs_path": str(demo_image.resolve()),
            "mime": "image/png" if demo_image.suffix.lower() == ".png" else "image/jpeg"
        }
        t0 = time.time()
        res_image = router.analyze_file(dummy_image_file, extracted_text="")
        t_img = time.time() - t0

        print(f"[OK] Réponse reçue en {t_img:.2f}s | Fournisseur: {res_image.get('provider_used')}")
        print(f"    - Catégorie  : {res_image.get('category')}")
        print(f"    - Mots-clés  : {res_image.get('keywords')}")
        print(f"    - Nom suggéré: {res_image.get('clean_name')}")
        print(f"    - Résumé     : {res_image.get('summary')}")
    else:
        print("\n[i] Aucune image de démo trouvée pour tester la vision.")

    print("\n" + "=" * 70)
    print("   TEST COMPLET RÉUSSI AVEC SUCCÈS")
    print("=" * 70)


if __name__ == "__main__":
    run_diagnostics()
