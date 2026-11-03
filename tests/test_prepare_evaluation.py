import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_evaluation import digest, prepare, validate

ROOT = Path(__file__).resolve().parents[1]


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.taxonomy = json.loads((ROOT / "evaluation/examples/taxonomy.json").read_text())
        self.rows = [
            json.loads(line)
            for line in (ROOT / "evaluation/examples/emails.jsonl").read_text().splitlines()
        ]

    def test_fixture_never_claims_acceptance(self):
        result = prepare(self.taxonomy, self.rows, synthetic=True, target_confirmed=False)
        self.assertEqual(result["status"], "fixture_only")
        self.assertFalse(result["quality_gates_passed"])
        self.assertEqual(result["counts"], {"tuning": 8, "held_out": 8})
        self.assertEqual(result["hardware_role"], "development_host_unconfirmed")

    def test_leakage_rejected(self):
        for field in ("source_id", "thread_id", "text"):
            with self.subTest(field=field):
                rows = copy.deepcopy(self.rows)
                rows[8][field] = rows[0][field]
                with self.assertRaisesRegex(ValueError, "leakage"):
                    validate(self.taxonomy, rows)

    def test_incomplete_coverage_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing case coverage"):
            validate(self.taxonomy, self.rows[:-1])
        with self.assertRaisesRegex(ValueError, "missing case coverage"):
            validate(self.taxonomy, self.rows, multilingual=True)

    def test_invalid_labels_and_ids_rejected(self):
        for field, value in [("id", self.rows[0]["id"]), ("label", "unknown"), ("split", "train")]:
            rows = copy.deepcopy(self.rows)
            rows[1][field] = value
            with self.assertRaises(ValueError):
                validate(self.taxonomy, rows)
        with self.assertRaises(ValueError):
            validate(self.taxonomy + [self.taxonomy[0]], self.rows)

    def test_hash_tracks_order_and_content(self):
        self.assertNotEqual(digest(self.taxonomy), digest(list(reversed(self.taxonomy))))
        changed = copy.deepcopy(self.taxonomy)
        changed[0]["description"] += " changed"
        self.assertNotEqual(digest(self.taxonomy), digest(changed))
        self.assertEqual(digest({"b": 2, "a": 1}), digest({"a": 1, "b": 2}))

    def test_cli_refuses_overwrite_and_does_not_copy_email_text(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "manifest.json"
            command = [
                sys.executable,
                str(ROOT / "scripts/prepare_evaluation.py"),
                "--taxonomy",
                str(ROOT / "evaluation/examples/taxonomy.json"),
                "--dataset",
                str(ROOT / "evaluation/examples/emails.jsonl"),
                "--data-kind",
                "synthetic",
                "--output",
                str(output),
            ]
            subprocess.run(command, check=True, capture_output=True)
            original = output.read_bytes()
            self.assertNotIn(b"Subject:", original)
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
