import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from email.message import Message
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "canvasctl.py"
SPEC = importlib.util.spec_from_file_location("canvasctl", MODULE_PATH)
canvasctl = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["canvasctl"] = canvasctl
SPEC.loader.exec_module(canvasctl)


class CanvasCtlTests(unittest.TestCase):
    def test_cookie_validation_accepts_raw_header_value(self):
        self.assertEqual(
            canvasctl.validate_cookie("one=1; two=2"),
            "one=1; two=2",
        )

    def test_cookie_validation_strips_optional_label(self):
        self.assertEqual(canvasctl.validate_cookie("Cookie: one=1"), "one=1")

    def test_cookie_validation_rejects_newline(self):
        with self.assertRaises(canvasctl.AuthenticationError):
            canvasctl.validate_cookie("one=1\nInjected: yes")

    def test_parse_link_header(self):
        header = (
            '<https://example.test/api?page=1>; rel="current", '
            '<https://example.test/api?page=2>; rel="next"'
        )
        self.assertEqual(
            canvasctl.parse_link_header(header)["next"],
            "https://example.test/api?page=2",
        )

    def test_sanitize_filename_prevents_path_traversal(self):
        value = canvasctl.sanitize_filename("../../secret\nfile.pdf")
        self.assertNotIn("/", value)
        self.assertNotIn("\n", value)
        self.assertTrue(value.endswith("file.pdf"))

    def test_extract_page_file_urls(self):
        body = (
            '<a href="/courses/1/files/99/download">Worksheet</a>'
            '<a href="https://example.org/readme.pdf">PDF</a>'
            '<a href="https://example.org/page">Not a file</a>'
        )
        urls = canvasctl.extract_page_file_urls(body, "https://canvas.example")
        self.assertEqual(len(urls), 2)
        self.assertIn("https://canvas.example/courses/1/files/99/download", urls)
        self.assertIn("https://example.org/readme.pdf", urls)

    def test_select_latest_module_by_position(self):
        modules = [
            {"id": 1, "name": "Week 1", "position": 1, "published": True},
            {"id": 2, "name": "Week 2", "position": 2, "published": True},
            {"id": 3, "name": "Draft", "position": 3, "published": False},
        ]
        selected = canvasctl.select_modules(
            modules, query=None, latest=1, all_modules=False
        )
        self.assertEqual([m["id"] for m in selected], [2])

    def test_select_module_by_case_insensitive_title(self):
        modules = [
            {"id": 7, "name": "Week of August 17", "position": 4, "published": True}
        ]
        selected = canvasctl.select_modules(
            modules, query="august 17", latest=1, all_modules=False
        )
        self.assertEqual(selected[0]["id"], 7)

    def test_subject_aliases(self):
        self.assertEqual(
            canvasctl.normalize_subjects("Maths,Social Studies"),
            ["math", "social"],
        )

    def test_deduplicate_ignores_query_tokens(self):
        records = [
            {"url": "https://files.test/a.pdf?token=one", "file_id": 9},
            {"url": "https://files.test/a.pdf?token=two", "file_id": 9},
        ]
        self.assertEqual(len(canvasctl.deduplicate_records(records)), 1)

    def test_simulated_download_writes_file_and_safe_manifest(self):
        class FakeClient:
            base_url = "https://canvas.example"

            def request(self, url, accept="application/json"):
                headers = Message()
                headers["Content-Type"] = "application/pdf"
                headers["Content-Disposition"] = 'attachment; filename="worksheet.pdf"'
                return canvasctl.ResponseData(
                    data=b"fake pdf bytes",
                    final_url="https://cdn.example/signed.pdf?token=secret",
                    headers=headers,
                )

        args = Namespace(
            output=None,
            subjects=["math"],
            module_query=None,
            latest=1,
            all_modules=False,
            dry_run=False,
        )
        modules = [
            {"id": 11, "name": "Week of August 17", "position": 9, "published": True}
        ]
        items = [{"id": 22, "type": "File", "title": "Worksheet"}]
        records = [
            {
                "url": "https://canvas.example/file?token=do-not-store",
                "filename": "worksheet.pdf",
                "file_id": 33,
                "source": "Worksheet",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            args.output = temp_dir
            with (
                patch.object(canvasctl, "get_modules", return_value=modules),
                patch.object(canvasctl, "get_module_items", return_value=items),
                patch.object(canvasctl, "discover_item_files", return_value=records),
            ):
                result = canvasctl.command_download(FakeClient(), args)
            self.assertEqual(result, 0)
            downloaded = Path(temp_dir) / "Maths" / "Week of August 17" / "worksheet.pdf"
            self.assertEqual(downloaded.read_bytes(), b"fake pdf bytes")
            manifest_path = Path(temp_dir) / "canvas-download-manifest.json"
            manifest_text = manifest_path.read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            self.assertEqual(manifest["subjects"]["Maths"][0]["files"][0]["file_id"], 33)
            self.assertNotIn("token", manifest_text)
            self.assertNotIn("cookie", manifest_text.lower())


if __name__ == "__main__":
    unittest.main()
