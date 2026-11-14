import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

# Load the extractor by path, matching test_analyze_agent_ui_capture.py: scripts/
# is deliberately outside pyright's include and is not an importable package, so
# `from scripts...` is unresolvable under the project config.
_MOD_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "dev" / "har-spike" / "extract_har_summary.py"
)
_spec = importlib.util.spec_from_file_location("extract_har_summary", _MOD_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
summarize_har = _mod.summarize_har


class HarSummaryTests(unittest.TestCase):
    def test_redacts_sensitive_headers_and_body_values(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://aisandbox-pa.googleapis.com/v1/flow:batchGenerateImages?key=secret",
                            "headers": [
                                {"name": "Content-Type", "value": "text/plain;charset=UTF-8"},
                                {"name": "Cookie", "value": "SID=secret; SAPISID=secret"},
                                {"name": "Authorization", "value": "Bearer secret"},
                                {"name": "Origin", "value": "https://labs.google"},
                            ],
                            "postData": {
                                "mimeType": "text/plain;charset=UTF-8",
                                "text": json.dumps(
                                    {
                                        "clientContext": {
                                            "recaptchaContext": {"token": "0cAFsecret"},
                                            "sessionId": "session-secret",
                                        },
                                        "requests": [
                                            {
                                                "prompt": "hello",
                                                "mediaGenerationContext": {
                                                    "batchId": "batch-secret"
                                                },
                                            }
                                        ],
                                    }
                                ),
                            },
                        },
                        "response": {
                            "status": 200,
                            "headers": [
                                {"name": "Content-Type", "value": "application/json"},
                                {"name": "Set-Cookie", "value": "private=value"},
                            ],
                            "content": {"mimeType": "application/json"},
                        },
                    }
                ]
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            har_path = Path(tmp) / "capture.har"
            har_path.write_text(json.dumps(har), encoding="utf-8")

            summary = summarize_har(har_path, host_filter="aisandbox-pa.googleapis.com")

        self.assertEqual(summary["entry_count"], 1)
        entry = summary["entries"][0]
        self.assertEqual(entry["request"]["method"], "POST")
        self.assertEqual(
            entry["request"]["url"],
            "https://aisandbox-pa.googleapis.com/v1/flow:batchGenerateImages",
        )
        self.assertEqual(entry["request"]["query_keys"], ["key"])
        self.assertEqual(entry["response"]["status"], 200)
        self.assertEqual(entry["request"]["headers"]["cookie"], "<redacted:present>")
        self.assertEqual(entry["request"]["headers"]["authorization"], "<redacted:present>")
        self.assertEqual(entry["response"]["headers"]["set-cookie"], "<redacted:present>")
        # A secret-shaped key now takes its whole subtree with it (#1276 review).
        self.assertEqual(
            entry["request"]["body"]["json"]["clientContext"]["recaptchaContext"],
            "<redacted:recaptchaContext>",
        )
        self.assertEqual(
            entry["request"]["body"]["json"]["clientContext"]["sessionId"],
            "<redacted:sessionId>",
        )
        self.assertEqual(
            entry["request"]["body"]["json"]["requests"][0]["mediaGenerationContext"]["batchId"],
            "<redacted:batchId>",
        )
        self.assertEqual(
            entry["request"]["body"]["json"]["requests"][0]["prompt"], "<redacted:prompt>"
        )

    def test_filters_to_requested_host(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "GET",
                            "url": "https://example.com/api",
                            "headers": [],
                        },
                        "response": {"status": 200, "headers": []},
                    },
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://aisandbox-pa.googleapis.com/v1/flow:generate",
                            "headers": [],
                        },
                        "response": {"status": 401, "headers": []},
                    },
                ]
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            har_path = Path(tmp) / "capture.har"
            har_path.write_text(json.dumps(har), encoding="utf-8")

            summary = summarize_har(har_path, host_filter="aisandbox-pa.googleapis.com")

        self.assertEqual(summary["entry_count"], 1)
        self.assertEqual(summary["entries"][0]["request"]["host"], "aisandbox-pa.googleapis.com")

    def test_redacts_non_json_body_preview(self) -> None:
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://aisandbox-pa.googleapis.com/v1/flow:generate",
                            "headers": [],
                            "postData": {
                                "mimeType": "text/plain",
                                "text": "prompt=private&token=secret",
                            },
                        },
                        "response": {"status": 200, "headers": []},
                    }
                ]
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            har_path = Path(tmp) / "capture.har"
            har_path.write_text(json.dumps(har), encoding="utf-8")

            summary = summarize_har(har_path, host_filter="aisandbox-pa.googleapis.com")

        body = summary["entries"][0]["request"]["body"]
        self.assertEqual(body["textPreview"], "<redacted:non-json>")

    def test_redacts_free_text_under_any_key_and_keeps_allowlisted_enums(self) -> None:
        body = {
            "parts": [{"text": "private prompt"}],
            "imageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            "videoModelKey": "veo_3_1_t2v_fast",
            "aspectRatio": "private prompt in an allowlisted key",
            "seed": 42,
            "enabled": True,
        }
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://aisandbox-pa.googleapis.com/v1/flow:generate",
                            "headers": [],
                            "postData": {"mimeType": "application/json", "text": json.dumps(body)},
                        },
                        "response": {"status": 200, "headers": []},
                    }
                ]
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            har_path = Path(tmp) / "capture.har"
            har_path.write_text(json.dumps(har), encoding="utf-8")

            summary = summarize_har(har_path, host_filter="aisandbox-pa.googleapis.com")

        self.assertNotIn("private prompt", json.dumps(summary))
        redacted = summary["entries"][0]["request"]["body"]["json"]
        self.assertEqual(redacted["parts"][0]["text"], "<redacted:str:14>")
        self.assertEqual(redacted["imageAspectRatio"], "IMAGE_ASPECT_RATIO_LANDSCAPE")
        self.assertEqual(redacted["videoModelKey"], "veo_3_1_t2v_fast")
        self.assertEqual(redacted["seed"], 42)
        self.assertIs(redacted["enabled"], True)


if __name__ == "__main__":
    unittest.main()


class ReviewRoundTests(unittest.TestCase):
    """#1276 review round: each assertion pins one leak or one survived mutant."""

    def test_location_and_referer_keep_no_query(self) -> None:
        signed = "https://storage.googleapis.com/b/o.png?GoogleAccessId=x%40y&Signature=abc"
        headers = _mod._headers_to_dict(
            [
                {"name": "Location", "value": signed},
                {"name": "Referer", "value": "https://flow.google.com/project/p?token=t"},
            ]
        )
        dumped = json.dumps(headers)
        self.assertNotIn("Signature=", dumped)
        self.assertNotIn("GoogleAccessId=", dumped)
        self.assertNotIn("token=t", dumped)
        self.assertEqual(headers["location"], "https://storage.googleapis.com/b/o.png")

    def test_everything_under_a_secret_key_is_redacted(self) -> None:
        out = _mod._redact_json({"session": {"id": 1234567890}, "token": ["a", "b"]})
        dumped = json.dumps(out)
        self.assertNotIn("1234567890", dumped)
        self.assertNotIn('"a"', dumped)

    def test_list_items_inherit_their_key(self) -> None:
        out = _mod._redact_json({"modelKey": ["veo_3_1_fast"], "text": ["a private prompt"]})
        self.assertEqual(out["modelKey"], ["veo_3_1_fast"])
        self.assertNotIn("private", json.dumps(out))

    def test_enum_values_are_capped_at_64_characters(self) -> None:
        out = _mod._redact_json({"tool": "a" * 65, "modelKey": "veo_3_1"})
        self.assertEqual(out["tool"], "<redacted:str:65>")
        self.assertEqual(out["modelKey"], "veo_3_1")

    def test_tool_name_and_update_mask_are_shown(self) -> None:
        out = _mod._redact_json({"toolName": "prompt_rewriter", "updateMask": "title"})
        self.assertEqual(out, {"toolName": "prompt_rewriter", "updateMask": "title"})
