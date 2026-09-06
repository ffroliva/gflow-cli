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
        self.assertEqual(
            entry["request"]["body"]["json"]["clientContext"]["recaptchaContext"]["token"],
            "<redacted:token>",
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


if __name__ == "__main__":
    unittest.main()
