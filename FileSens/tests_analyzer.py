import unittest
import json
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import shutil
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock

import app as app_module
import cli as cli_module
from core.ai_router import AIRouter, KeyRotator, has_specific_content_keywords
from core.analysis_cache import needs_content_keyword_refresh
from config import AI_CONFIG_SIGNATURE

from core.scanner import scan_directory, get_file_family
from core.renamer import generate_safe_filename, is_filename_non_explicit
from core.extractors.ocr_engine import ocr_image
from core.file_ops import FileOperations
from core.classifier import FileClassifier

class TestFileAnalyzer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = Path("/tmp/test_file_analyzer")
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir)
        cls.test_dir.mkdir(parents=True, exist_ok=True)

        # 1. Créer une image avec du texte pour tester l'OCR
        cls.img_path = cls.test_dir / "IMG_20240115_001.png"
        img = Image.new('RGB', (400, 150), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((20, 30), "FACTURE HOTEL IBIS", fill=(0, 0, 0))
        draw.text((20, 70), "Total TTC: 145.50 EUR", fill=(0, 0, 0))
        img.save(cls.img_path)

        # 2. Créer un fichier texte
        cls.txt_path = cls.test_dir / "temp_note_123.txt"
        with open(cls.txt_path, "w", encoding="utf-8") as f:
            f.write("Rapport de réunion d'architecture technique. Sujet: Déploiement cloud.")

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir)

    def test_scanner(self):
        files = scan_directory(self.test_dir)
        self.assertEqual(len(files), 2)
        filenames = [f["filename"] for f in files]
        self.assertIn("IMG_20240115_001.png", filenames)
        self.assertIn("temp_note_123.txt", filenames)

    def test_web_rejects_untrusted_host(self):
        client = app_module.app.test_client()
        self.assertEqual(client.get("/", headers={"Host": "localhost:7885"}).status_code, 200)
        self.assertEqual(client.get("/", headers={"Host": "example.com"}).status_code, 400)

    def test_scans_of_different_roots_keep_separate_files(self):
        self.addCleanup(app_module.ANALYSIS_CACHE.clear)
        with TemporaryDirectory(dir=self.test_dir) as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "same.txt").write_text("premier", encoding="utf-8")
            (second_dir / "same.txt").write_text("second", encoding="utf-8")

            class FakeClassifier:
                def process_file(self, file_info):
                    return file_info

            with patch.object(app_module, "FileClassifier", FakeClassifier):
                client = app_module.app.test_client()
                first = client.post("/api/scan", json={"directory": str(first_dir)}).get_json()
                second = client.post("/api/scan", json={"directory": str(second_dir)}).get_json()
                repeated = client.post("/api/scan", json={"directory": str(first_dir)}).get_json()

            self.assertNotEqual(first["scan_id"], second["scan_id"])
            self.assertNotEqual(first["scan_id"], repeated["scan_id"])
            first_id = first["files"][0]["id"]
            second_id = second["files"][0]["id"]
            self.assertNotEqual(first_id, second_id)
            self.assertEqual(first_id, repeated["files"][0]["id"])
            with client.get(f"/api/preview/{first_id}", query_string={"scan_id": first["scan_id"]}) as response:
                self.assertEqual(response.data, b"premier")
            with client.get(f"/api/preview/{second_id}", query_string={"scan_id": second["scan_id"]}) as response:
                self.assertEqual(response.data, b"second")
            self.assertEqual(
                client.get(f"/api/preview/{first_id}", query_string={"scan_id": second["scan_id"]}).status_code,
                404
            )
            renamed = client.post("/api/rename", json={
                "scan_id": first["scan_id"],
                "file_id": first_id,
                "new_filename": "renamed.txt"
            })
            self.assertEqual(renamed.status_code, 200)

            # Test preview-docx fallback
            docx_resp = client.get(f"/api/preview-docx/{first_id}", query_string={"scan_id": first["scan_id"]})
            self.assertEqual(docx_resp.status_code, 200)
            self.assertIn("html", docx_resp.get_json())
            with client.get(f"/api/preview/{first_id}", query_string={"scan_id": first["scan_id"]}) as response:
                self.assertEqual(response.data, b"premier")
            self.assertEqual(
                client.get(f"/api/preview/{first_id}", query_string={"scan_id": repeated["scan_id"]}).status_code,
                404
            )

    def test_web_scan_resumes_after_interruption(self):
        self.addCleanup(app_module.ANALYSIS_CACHE.clear)
        with TemporaryDirectory(dir=self.test_dir) as temp_dir:
            root = Path(temp_dir)
            for name in ("a.txt", "b.txt", "c.txt"):
                (root / name).write_text(name, encoding="utf-8")
            calls = []

            class InterruptingClassifier:
                def process_file(self, file_info):
                    calls.append(file_info["filename"])
                    if file_info["filename"] == "b.txt":
                        raise RuntimeError("interrupted")
                    return file_info

            client = app_module.app.test_client()
            with patch.object(app_module, "FileClassifier", InterruptingClassifier):
                response = client.post("/api/scan", json={"directory": str(root)})
            self.assertEqual(response.status_code, 500)
            cache_file = root / ".filesens_cache.json"
            self.assertEqual(
                [item["filename"] for item in json.loads(cache_file.read_text(encoding="utf-8"))],
                ["a.txt"],
            )

            class ResumingClassifier:
                def process_file(self, file_info):
                    calls.append(file_info["filename"])
                    return file_info

            with patch.object(app_module, "FileClassifier", ResumingClassifier):
                response = client.post("/api/scan", json={"directory": str(root)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(calls, ["a.txt", "b.txt", "b.txt", "c.txt"])
            self.assertEqual(response.get_json()["total_files"], 3)

    def test_web_scan_can_be_cancelled(self):
        self.addCleanup(app_module.ANALYSIS_CACHE.clear)
        with TemporaryDirectory(dir=self.test_dir) as temp_dir:
            root = Path(temp_dir)
            for name in ("f1.txt", "f2.txt", "f3.txt"):
                (root / name).write_text(name, encoding="utf-8")

            class CancellingClassifier:
                def process_file(self, file_info):
                    if file_info["filename"] == "f1.txt":
                        # Simule l'appel de l'annulation pendant le traitement
                        app_module.SCAN_CANCEL_EVENT.set()
                    return file_info

            client = app_module.app.test_client()
            with patch.object(app_module, "FileClassifier", CancellingClassifier):
                response = client.post("/api/scan", json={"directory": str(root)})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data.get("was_cancelled"))
            self.assertEqual(data.get("status"), "cancelled")
            self.assertEqual(len(data.get("files", [])), 1)

    def test_cli_scan_resumes_after_interruption(self):
        with TemporaryDirectory(dir=self.test_dir) as temp_dir:
            root = Path(temp_dir)
            for name in ("a.txt", "b.txt", "c.txt"):
                (root / name).write_text(name, encoding="utf-8")
            calls = []

            class InterruptingClassifier:
                def process_file(self, file_info):
                    calls.append(file_info["filename"])
                    if file_info["filename"] == "b.txt":
                        raise RuntimeError("interrupted")
                    return {**file_info, "category": "Divers", "keywords": [],
                            "suggested_filename": file_info["filename"], "summary": ""}

            with patch.object(cli_module, "FileClassifier", InterruptingClassifier), patch.object(
                sys, "argv", ["cli.py", str(root)]
            ):
                with self.assertRaises(RuntimeError):
                    cli_module.run_cli()
            cache_file = root / ".filesens_cache.json"
            self.assertEqual(len(json.loads(cache_file.read_text(encoding="utf-8"))), 1)

            class ResumingClassifier:
                def process_file(self, file_info):
                    calls.append(file_info["filename"])
                    return {**file_info, "category": "Divers", "keywords": [],
                            "suggested_filename": file_info["filename"], "summary": ""}

            with patch.object(cli_module, "FileClassifier", ResumingClassifier), patch.object(
                sys, "argv", ["cli.py", str(root)]
            ):
                cli_module.run_cli()
            self.assertEqual(calls, ["a.txt", "b.txt", "b.txt", "c.txt"])
            self.assertEqual(len(json.loads(cache_file.read_text(encoding="utf-8"))), 3)

    def test_content_files_require_two_specific_llm_keywords(self):
        for family, ext, generic in (
            ("pdf", ".pdf", "Document PDF"), ("image", ".png", "Photo"),
            ("document", ".docx", "Bureautique"),
            ("document", ".pptx", "PowerPoint"),
            ("document", ".xlsx", "Tableur"),
        ):
            with self.subTest(ext=ext):
                router = AIRouter()
                router.maia_rotator = KeyRotator([])
                router.deepseek_rotator = KeyRotator([])
                router.gemini_rotator = KeyRotator(["test"])
                router.groq_rotator = KeyRotator(["test"])
                router.openrouter_rotator = KeyRotator([])
                file_info = {"family": family, "ext": ext, "filename": f"test{ext}", "stem": "test"}
                bad = {"keywords": [generic, "Montagne"], "category": "Divers"}
                good = {"keywords": ["Montagne enneigée", "Lac alpin"], "category": "Paysages"}
                with patch.object(router, "_try_gemini", return_value=bad), patch.object(
                    router, "_try_groq", return_value=good
                ):
                    result = router.analyze_file(file_info, extracted_text="Montagne et lac")
                self.assertEqual(result["keywords"], good["keywords"])
                self.assertEqual(result["provider_used"], "Groq")
                self.assertFalse(has_specific_content_keywords(bad))
                self.assertTrue(needs_content_keyword_refresh({"family": family, "keywords": [generic]}))
                self.assertTrue(needs_content_keyword_refresh({
                    "family": family, "keywords": ["Facture", "Paiement"],
                    "provider_used": "Heuristique Locale (Sans IA)",
                }))
                self.assertFalse(needs_content_keyword_refresh({
                    "family": family, "keywords": good["keywords"], "provider_used": "Groq",
                }))
                self.assertIn("exactement 2 mots-clés", router._build_prompt(file_info, "", family == "image"))
                self.assertEqual(get_file_family(ext, ""), family)

    def test_content_files_without_llm_have_no_generic_keywords(self):
        for family in ("pdf", "image", "document"):
            with self.subTest(family=family):
                router = AIRouter()
                router.maia_rotator = KeyRotator([])
                router.deepseek_rotator = KeyRotator([])
                router.gemini_rotator = KeyRotator([])
                router.groq_rotator = KeyRotator([])
                router.openrouter_rotator = KeyRotator([])
                result = router.analyze_file(
                    {"family": family, "filename": "facture.pdf", "stem": "facture", "size_formatted": "1 Ko"},
                    extracted_text="Facture avec montant TTC",
                )
                self.assertEqual(result["keywords"], [])
                self.assertEqual(result["provider_used"], "Heuristique Locale (Sans IA)")

    def test_deepseek_credit_exhaustion_uses_next_key(self):
        router = AIRouter()
        router.maia_rotator = KeyRotator([])
        router.deepseek_rotator = KeyRotator(["first-test-key", "second-test-key"])
        router.gemini_rotator = KeyRotator([])
        router.groq_rotator = KeyRotator([])
        router.openrouter_rotator = KeyRotator([])
        response_ok = Mock(status_code=200)
        response_ok.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "category": "Documents & Rapports",
            "keywords": ["Transition énergétique", "Énergie solaire"],
            "summary": "Rapport sur l'énergie solaire.", "clean_name": "Energie_Solaire",
        })}}]}
        with patch("core.ai_router.requests.post", side_effect=[Mock(status_code=402), response_ok]) as post:
            result = router.analyze_file(
                {"family": "document", "ext": ".docx", "filename": "energie.docx"},
                extracted_text="Rapport sur la transition énergétique et l'énergie solaire.",
            )
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer first-test-key")
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer second-test-key")
        self.assertEqual(result["provider_used"], "DeepSeek")
        self.assertEqual(result["keywords"], ["Transition énergétique", "Énergie solaire"])
        self.assertIn("crédit épuisé", result["ai_warnings"][0])
        self.assertNotIn("first-test-key", str(result["ai_warnings"]))
        self.assertEqual(router.deepseek_rotator.available_count, 1)

    def test_deepseek_credit_exhaustion_falls_back_to_other_provider(self):
        router = AIRouter()
        router.maia_rotator = KeyRotator([])
        router.deepseek_rotator = KeyRotator(["test-key"])
        router.gemini_rotator = KeyRotator(["test-key"])
        router.groq_rotator = KeyRotator([])
        router.openrouter_rotator = KeyRotator([])
        gemini_result = {"category": "Divers", "keywords": ["Énergie solaire", "Réseau électrique"]}
        with patch("core.ai_router.requests.post", return_value=Mock(status_code=402)), patch.object(
            router, "_try_gemini", return_value=gemini_result
        ) as gemini:
            result = router.analyze_file({"family": "document", "filename": "rapport.docx"})
        gemini.assert_called_once()
        self.assertEqual(result["provider_used"], "Google Gemini")
        self.assertIn("crédit épuisé", result["ai_warnings"][0])

    def test_deepseek_image_request_contains_image(self):
        router = AIRouter()
        router.maia_rotator = KeyRotator([])
        router.deepseek_rotator = KeyRotator(["test-key"])
        router.gemini_rotator = KeyRotator([])
        router.groq_rotator = KeyRotator([])
        router.openrouter_rotator = KeyRotator([])
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "category": "Photographies & Paysages",
            "keywords": ["Montagne enneigée", "Lac alpin"],
            "summary": "Paysage de montagne.", "clean_name": "Montagne_Lac",
        })}}]}
        with patch("core.ai_router.encode_image_base64", return_value="encoded"), patch(
            "core.ai_router.requests.post", return_value=response
        ) as post:
            result = router.analyze_file({"family": "image", "abs_path": "/tmp/test.png", "filename": "test.png"})
        content = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(content[1]["image_url"]["url"], "data:image/jpeg;base64,encoded")
        self.assertEqual(result["provider_used"], "DeepSeek")

    def test_rate_limit_is_not_reported_as_exhausted_credit(self):
        router = AIRouter()
        router.maia_rotator = KeyRotator([])
        router.deepseek_rotator = KeyRotator(["test-key"])
        router.gemini_rotator = KeyRotator([])
        router.groq_rotator = KeyRotator([])
        router.openrouter_rotator = KeyRotator([])
        with patch("core.ai_router.requests.post", return_value=Mock(status_code=429)):
            result = router.analyze_file({"family": "document", "filename": "rapport.docx"})
        self.assertEqual(result["provider_used"], "Heuristique Locale (Sans IA)")
        self.assertIn("limite de requêtes", result["ai_warnings"][0])
        self.assertNotIn("crédit épuisé", result["ai_warnings"][0])
        self.assertEqual(router.deepseek_rotator.available_count, 1)

    def test_cached_local_result_retries_only_after_ai_configuration_change(self):
        cached = {"family": "document", "keywords": [],
                  "provider_used": "Heuristique Locale (Sans IA)",
                  "ai_config_signature": AI_CONFIG_SIGNATURE}
        self.assertFalse(needs_content_keyword_refresh(cached))
        self.assertTrue(needs_content_keyword_refresh({**cached, "ai_config_signature": "old"}))

    def test_web_scan_returns_credit_warning(self):
        self.addCleanup(app_module.ANALYSIS_CACHE.clear)
        with TemporaryDirectory(dir=self.test_dir) as temp_dir:
            root = Path(temp_dir)
            (root / "rapport.txt").write_text("Rapport", encoding="utf-8")

            class WarningClassifier:
                def process_file(self, file_info):
                    return {**file_info, "category": "Divers", "keywords": [],
                            "suggested_filename": file_info["filename"], "summary": "",
                            "ai_warnings": ["DeepSeek : crédit épuisé pour la clé 1."]}

            with patch.object(app_module, "FileClassifier", WarningClassifier):
                response = app_module.app.test_client().post("/api/scan", json={"directory": str(root)})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["warnings"], ["DeepSeek : crédit épuisé pour la clé 1."])

    def test_non_explicit_detection(self):
        self.assertTrue(is_filename_non_explicit("IMG_20240115_001"))
        self.assertTrue(is_filename_non_explicit("DSC_0042"))
        self.assertTrue(is_filename_non_explicit("scan_001"))
        self.assertTrue(is_filename_non_explicit("temp_note_123"))
        self.assertFalse(is_filename_non_explicit("Facture_Hotel_Ibis_2024"))

    def test_ocr_extraction(self):
        text = ocr_image(self.img_path)
        self.assertIn("FACTURE", text.upper())
        self.assertIn("HOTEL", text.upper())

    def test_safe_renaming_with_date(self):
        name1, should_rename = generate_safe_filename(
            target_dir=self.test_dir,
            clean_keyword="Facture_Hotel",
            date_stamp="20240115",
            extension=".png",
            current_stem="IMG_20240115_001"
        )
        self.assertTrue(should_rename)
        self.assertEqual(name1, "Facture_Hotel_20240115.png")

        # Simuler l'existence pour tester la collision anti-écrasement
        existing = self.test_dir / name1
        existing.touch()
        name2, _ = generate_safe_filename(
            target_dir=self.test_dir,
            clean_keyword="Facture_Hotel",
            date_stamp="20240115",
            extension=".png",
            current_stem="IMG_20240115_002"
        )
        self.assertEqual(name2, "Facture_Hotel_20240115_2.png")
        existing.unlink()

    def test_file_operations(self):
        ops = FileOperations(self.test_dir)
        # Test renommage
        dummy_file = self.test_dir / "to_rename.txt"
        dummy_file.write_text("test")
        new_path = ops.rename_file(dummy_file, "renamed_test.txt")
        self.assertTrue(new_path.exists())
        self.assertEqual(new_path.name, "renamed_test.txt")

        # Test déplacement dans une catégorie
        moved = ops.move_file(new_path, "Archives")
        self.assertTrue(moved.exists())
        self.assertEqual(moved.parent.name, "Archives")

        # Test corbeille
        trashed = ops.trash_file(moved)
        self.assertTrue(trashed.exists())
        self.assertEqual(trashed.parent.name, ".trash")

    def test_maia_is_used_as_first_priority(self):
        router = AIRouter()
        router.maia_rotator = KeyRotator(["test-maia-key"])
        router.deepseek_rotator = KeyRotator(["test-deepseek-key"])
        router.gemini_rotator = KeyRotator(["test-gemini-key"])

        maia_res = {
            "category": "Factures & Reçus",
            "keywords": ["Facture EDF", "Électricité"],
            "summary": "Facture d'électricité mensuelle.",
            "clean_name": "Facture_EDF"
        }

        with patch.object(router, "_try_maia", return_value=maia_res) as mock_maia, \
             patch.object(router, "_try_deepseek") as mock_deepseek:
            result = router.analyze_file(
                {"family": "document", "ext": ".pdf", "filename": "facture_edf.pdf"},
                extracted_text="Facture Electricité EDF"
            )

        mock_maia.assert_called_once()
        mock_deepseek.assert_not_called()
        self.assertEqual(result["provider_used"], "Maïa CEA")
        self.assertEqual(result["keywords"], ["Facture EDF", "Électricité"])

    def test_maia_text_and_image_models(self):
        router = AIRouter()
        router.maia_rotator = KeyRotator(["test-maia-key"])

        # Test texte -> doit utiliser MAIA_TEXT_MODEL (mistralai-small)
        with patch("core.ai_router.requests.post") as mock_post:
            mock_resp = Mock(status_code=200)
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": json.dumps({
                    "category": "Documents & Rapports",
                    "keywords": ["Note Synthèse", "Recherche"],
                    "summary": "Note de synthèse.",
                    "clean_name": "Note_Synthese"
                })}}]
            }
            mock_post.return_value = mock_resp

            res_txt = router._try_maia(
                {"family": "document", "ext": ".docx", "filename": "note.docx"},
                extracted_text="Contenu de la note",
                is_image=False
            )
            self.assertIsNotNone(res_txt)
            sent_payload_txt = mock_post.call_args.kwargs["json"]
            self.assertEqual(sent_payload_txt["model"], "mistralai-small")

        # Test image -> doit utiliser MAIA_VISION_MODEL (mistralai-small)
        with patch("core.ai_router.requests.post") as mock_post, \
             patch("core.ai_router.encode_image_base64", return_value="dummy_b64"):
            mock_resp = Mock(status_code=200)
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": json.dumps({
                    "category": "Photographies & Paysages",
                    "keywords": ["Montagne Neige", "Sommet"],
                    "summary": "Photo de montagne.",
                    "clean_name": "Photo_Montagne"
                })}}]
            }
            mock_post.return_value = mock_resp

            res_img = router._try_maia(
                {"family": "image", "ext": ".png", "filename": "photo.png", "abs_path": str(self.img_path)},
                extracted_text="",
                is_image=True
            )
            self.assertIsNotNone(res_img)
            sent_payload_img = mock_post.call_args.kwargs["json"]
            self.assertEqual(sent_payload_img["model"], "mistralai-small")
            # Vérifier que le format multimodal image_url est bien présent
            msg_content = sent_payload_img["messages"][0]["content"]
            self.assertTrue(any(item.get("type") == "image_url" for item in msg_content))

    def test_maia_timeout_falls_back_gracefully(self):
        import requests
        router = AIRouter()
        router.maia_rotator = KeyRotator(["test-maia-key"])
        router.deepseek_rotator = KeyRotator([])
        router.gemini_rotator = KeyRotator([])
        router.groq_rotator = KeyRotator([])
        router.openrouter_rotator = KeyRotator([])

        with patch("core.ai_router.requests.post", side_effect=requests.exceptions.ReadTimeout("Read timed out")):
            result = router.analyze_file(
                {"family": "document", "ext": ".docx", "filename": "rapport.docx"},
                extracted_text="Rapport financier 2024"
            )
        self.assertEqual(result["provider_used"], "Heuristique Locale (Sans IA)")
        self.assertTrue(any("délai d'attente dépassé" in w or "timeout" in w.lower() for w in result["ai_warnings"]))

    def test_environment_cea_detection(self):
        from core.environment import get_environment_info, is_cea_network
        info = get_environment_info(refresh=True)
        self.assertIn("is_cea", info)
        self.assertIn("environment_label", info)
        self.assertIn("hostname", info)
        self.assertIn("local_ips", info)
        self.assertIsInstance(is_cea_network(), bool)

    def test_external_mode_never_uses_maia(self):
        """Vérifie qu'en mode 'external', Maïa n'est JAMAIS appelé même au CEA avec des clés."""
        router = AIRouter(mode="external")
        router.maia_rotator = KeyRotator(["maia-key-1"])
        router.gemini_rotator = KeyRotator([])
        router.deepseek_rotator = KeyRotator([])
        router.groq_rotator = KeyRotator([])
        router.openrouter_rotator = KeyRotator([])

        self.assertFalse(router.should_use_maia())
        with patch.object(router, "_try_maia") as mock_maia:
            result = router.analyze_file({"family": "document", "filename": "rapport.pdf"})
            mock_maia.assert_not_called()
        self.assertEqual(result["provider_used"], "Heuristique Locale (Sans IA)")

    def test_auto_mode_outside_cea_skips_maia(self):
        """Vérifie qu'en mode 'auto' sur un VPS/hors CEA, Maïa est ignoré directement."""
        router = AIRouter(mode="auto")
        router.maia_rotator = KeyRotator(["maia-key-1"])
        with patch("core.environment.is_cea_network", return_value=False):
            self.assertFalse(router.should_use_maia())

    def test_gemini_multi_keys_rotation_on_quota(self):
        """Vérifie la rotation automatique sur les clés Gemini du .env lors d'un quota 429."""
        router = AIRouter(mode="external")
        router.gemini_rotator = KeyRotator(["gemini-key-1", "gemini-key-2"])
        router.groq_rotator = KeyRotator([])
        router.openrouter_rotator = KeyRotator([])
        router.deepseek_rotator = KeyRotator([])

        good_gemini = {
            "category": "Photographies & Paysages",
            "keywords": ["Montagne enneigée", "Lac alpin"],
            "summary": "- Paysage naturel.",
            "clean_name": "Montagne_Lac"
        }

        # Simuler échec de la 1ère clé (429 RESOURCE_EXHAUSTED) puis succès sur la 2ème clé
        side_effects = [
            Exception("429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric 'Generate Content API requests per day'"),
            Mock(text=json.dumps(good_gemini))
        ]

        mock_genai_mod = Mock()
        mock_instance = Mock()
        mock_genai_mod.Client.return_value = mock_instance
        mock_genai_mod.types = Mock()
        mock_instance.models.generate_content.side_effect = side_effects

        with patch.dict("sys.modules", {"google.genai": mock_genai_mod}):
            result = router.analyze_file({"family": "image", "abs_path": str(self.img_path), "filename": "test.png"})

        self.assertEqual(result["provider_used"], "Google Gemini")
        self.assertEqual(result["keywords"], ["Montagne enneigée", "Lac alpin"])
        self.assertTrue(any("quota" in w.lower() or "429" in w for w in result["ai_warnings"]))
        # La 1ère clé a été désactivée pour la session, il reste 1 clé
        self.assertEqual(router.gemini_rotator.available_count, 1)

    def test_cascade_to_groq_when_all_gemini_keys_exhausted(self):
        """Vérifie la bascule sur Groq lorsque toutes les clés Gemini sont épuisées."""
        router = AIRouter(mode="external")
        router.gemini_rotator = KeyRotator(["exhausted-gemini-key"])
        router.groq_rotator = KeyRotator(["valid-groq-key"])
        router.openrouter_rotator = KeyRotator([])
        router.deepseek_rotator = KeyRotator([])

        good_groq = {
            "category": "Photographies & Paysages",
            "keywords": ["Forêt boréale", "Aurore boréale"],
            "summary": "- Phénomène naturel.",
            "clean_name": "Aurore_Boreale"
        }

        with patch.object(router, "_try_gemini", return_value=None), patch.object(
            router, "_try_groq", return_value=good_groq
        ) as mock_groq:
            result = router.analyze_file({"family": "image", "filename": "aurore.jpg", "abs_path": str(self.img_path)})

        mock_groq.assert_called_once()
        self.assertEqual(result["provider_used"], "Groq")
        self.assertEqual(result["keywords"], good_groq["keywords"])

    def test_web_environment_api_includes_modes_and_keys(self):
        """Vérifie que l'endpoint /api/environment renvoie le mode configuré et le résumé des clés."""
        client = app_module.app.test_client()
        resp = client.get("/api/environment")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("configured_mode", data)
        self.assertIn("keys_summary", data)
        self.assertIn("gemini", data["keys_summary"])
        self.assertIn("groq", data["keys_summary"])

if __name__ == "__main__":
    unittest.main()
