from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from machine import run_cpu
from translator import AbstractSyntaxTree, MachineCode


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "golden"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


class GoldenTest(unittest.TestCase):
    def test_golden_programs(self) -> None:
        golden_files = sorted(GOLDEN_DIR.glob("*.json"))
        self.assertTrue(golden_files, "No golden fixtures found")

        for golden_file in golden_files:
            with self.subTest(golden=golden_file.name):
                spec = json.loads(golden_file.read_text(encoding="utf-8"))
                source = ROOT / spec["source"]
                schedule = ROOT / spec["schedule"] if spec.get("schedule") else None

                with tempfile.TemporaryDirectory() as tmp:
                    target = Path(tmp) / f"{golden_file.stem}.bin"
                    log = Path(tmp) / f"{golden_file.stem}.log"
                    MachineCode(AbstractSyntaxTree(str(source))).store(str(target))
                    output, ticks = run_cpu(
                        str(target),
                        str(target.with_suffix(target.suffix + ".config.json")),
                        schedule_file=str(schedule) if schedule else None,
                        log_file=str(log),
                    )

                    config = json.loads(
                        target.with_suffix(target.suffix + ".config.json").read_text(encoding="utf-8")
                    )
                    log_text = log.read_text(encoding="utf-8")

                    self.assertEqual(spec["output"], output)
                    self.assertEqual(spec["ticks"], ticks)
                    self.assertEqual(spec["source_sha256"], sha256_bytes(source.read_bytes()))
                    self.assertEqual(spec["machine_code_sha256"], sha256_bytes(target.read_bytes()))
                    self.assertEqual(spec["data_image_sha256"], sha256_json(config["data_image"]))
                    self.assertEqual(spec["config_sha256"], sha256_json(config))

                    for fragment in spec["log_contains"]:
                        self.assertIn(fragment, log_text)


if __name__ == "__main__":
    unittest.main()
