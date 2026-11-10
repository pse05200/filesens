# 🚀 FileScan AI : Analyseur, Trieur & Organiseur Intelligent de Fichiers

Application complète d'analyse, classification, OCR, vision multimodale, renommage avec date et tri de fichiers d'un répertoire.

---

## ✨ Fonctionnalités Principales

- **Détection Automatique & Scan Rapide** : Parcourt un dossier (récursif ou non) et analyse tous les types de fichiers (images, PDF, Word, Excel, textes, code).
- **OCR Intelligent** : Bascule automatique en reconnaissance optique de caractères (Tesseract) pour les PDF scannés et les images de documents (factures, reçus).
- **Cascade d'IA Multimodale (Fallback automatique)** :
  1. **DeepSeek Flash** pour les documents et les images.
  2. **Google AI Studio (Gemini)** en secours.
  3. **Groq API** en secours.
  4. **OpenRouter API** en secours.
  5. **Repli heuristique local** si aucun fournisseur ne répond.
- **Renommage Intelligent & Anti-Écrasement** :
  - Détecte les noms non explicites (ex: `IMG_20240315.png`, `DSC_0099.jpg`, `scan_0042.pdf`).
  - Propose un nom clair avec date : `[MotCle_Simple]_[Date].[ext]` (ex: `Facture_Hotel_20240315_1423.png`, `Paysage_Montagne_20240315.jpg`).
  - Garantie stricte anti-écrasement (indexation automatique `_2`, `_3` si collision).
- **Gestion Complète des Fichiers** :
  - **Déplacer** vers des dossiers classés par catégorie (ex: `Factures/`, `Photos/`, etc.) ou vers un sous-dossier personnalisé.
  - **Supprimer** en un clic vers une corbeille sécurisée (`.trash/`) ou définitivement.
- **Interface Hybride Ultra-Rapide (Web & CLI)** :
  - **Split-View** : Défilement fluide de la liste à gauche, panneau d'aperçu haute résolution et résumé à droite.
  - **Speed Triage au Clavier** : Naviguez avec `↑`/`↓`, renommez avec `R`, déplacez avec `M`, supprimez avec `Suppr`.
  - **Mode Grille / Galerie** : Vue panoramique en 1 clic.
  - **Sélection par lot** pour traiter plusieurs fichiers d'un coup.

---

## 🛠️ Configuration des Clés API

Créez un fichier `.env` à la racine (en vous basant sur `.env.example`) :

```bash
cp .env.example .env
```

Éditez le fichier `.env` avec vos clés :
```env
# Clés DeepSeek (plusieurs clés possibles séparées par des virgules)
DEEPSEEK_API_KEYS="sk-...,sk-..."
DEEPSEEK_MODEL="deepseek-flash"

# Clés Google AI Studio
GEMINI_API_KEYS="AIzaSy...,AIzaSy..."

# Clés Groq
GROQ_API_KEYS="gsk_...,gsk_..."

# Clés OpenRouter
OPENROUTER_API_KEYS="sk-or-..."
```

En cas de crédit épuisé (HTTP 402), l'application affiche un avertissement et essaye la clé suivante, puis les autres fournisseurs. La limite de débit (HTTP 429) est signalée séparément. Redémarrez le serveur après une modification de `.env` ; les anciens résultats locaux sont alors réanalysés avec la nouvelle configuration.

---

## 🖥️ Utilisation

### 1. Interface Web (Recommandée)

Lancez le serveur Flask :
```bash
python3 app.py
```
Ouvrez ensuite votre navigateur sur **http://localhost:7885**. Le serveur écoute uniquement sur la machine locale.

- Entrez le chemin du dossier à analyser et cliquez sur **Analyser**.
- Les résultats sont enregistrés après chaque fichier dans `.filesens_cache.json`. Après une interruption, relancez **Analyser** sur le même dossier pour reprendre aux fichiers restants. Le bouton de nouvelle analyse force le retraitement complet.
- Pour les PDF, images et documents bureautiques (Word, présentations, tableurs, etc.), l'analyse IA doit fournir deux mots-clés distincts sur le contenu. Si elle est indisponible, aucun mot-clé générique comme « Document PDF », « Photo » ou « Bureautique » n'est affiché. Les anciens résultats génériques sont réanalysés au prochain scan.
- Utilisez les flèches du clavier `↑` et `↓` pour inspecter les fichiers à la volée.
- Appuyez sur **`R`** pour renommer instantanément avec le nom suggéré + date.
- Appuyez sur **`M`** pour ranger le fichier dans le dossier de sa catégorie.
- Appuyez sur **`Suppr`** pour l'envoyer à la corbeille.

---

### 2. Interface en Ligne de Commande (CLI)

```bash
# Analyser un dossier avec affichage du tableau synthétique
python3 cli.py /chemin/vers/dossier

# Mode triage interactif au clavier (fichier par fichier)
python3 cli.py /chemin/vers/dossier --interactive

# Renommer automatiquement tous les fichiers aux noms non-explicites
python3 cli.py /chemin/vers/dossier --rename-auto

# Classer physiquement tous les fichiers dans des dossiers par catégorie
python3 cli.py /chemin/vers/dossier --organize

# Exporter un rapport complet en JSON ou CSV
python3 cli.py /chemin/vers/dossier --export-json rapport.json --export-csv rapport.csv
```

Après une interruption, relancez la même commande : les fichiers déjà analysés sont repris depuis `.filesens_cache.json` et seuls les fichiers restants sont traités.

---

## 🧪 Tests Unitaires

Pour vérifier que tous les modules fonctionnent correctement :
```bash
python3 tests_analyzer.py
```
