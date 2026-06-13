from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from machine import run_cpu
from translator import AbstractSyntaxTree, MachineCode


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "golden"


def read_json(filename: Path) -> object:
    return json.loads(filename.read_text(encoding="utf-8"))


class GoldenTest(unittest.TestCase):
    maxDiff = None

    def test_golden_programs(self) -> None:
        manifests = sorted(GOLDEN_DIR.glob("*/manifest.json"))
        self.assertTrue(manifests, "No golden fixture directories found")

        for manifest_file in manifests:
            with self.subTest(golden=manifest_file.parent.name):
                self.check_case(manifest_file)

    def check_case(self, manifest_file: Path) -> None:
        case_dir = manifest_file.parent
        manifest = read_json(manifest_file)
        source = case_dir / str(manifest["source"])
        schedule_name = manifest.get("schedule")
        schedule = case_dir / str(schedule_name) if schedule_name else None

        expected_binary = (case_dir / "program.bin").read_bytes()
        expected_listing = (case_dir / "program.lst").read_text(encoding="utf-8")
        expected_config = read_json(case_dir / "config.json")
        expected_trace = (case_dir / "trace.log").read_text(encoding="utf-8")
        expected_stdout = (case_dir / "stdout.txt").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "program.bin"
            log = Path(tmp) / "trace.log"
            MachineCode(AbstractSyntaxTree(str(source))).store(str(target))

            output, ticks = run_cpu(
                str(target),
                str(target.with_suffix(target.suffix + ".config.json")),
                schedule_file=str(schedule) if schedule else None,
                log_file=str(log),
            )

            actual_stdout = f"{output}\nticks: {ticks}\n"

            self.assertEqual(expected_binary, target.read_bytes())
            self.assertEqual(expected_listing, target.with_suffix(target.suffix + ".lst").read_text(encoding="utf-8"))
            self.assertEqual(expected_config, read_json(target.with_suffix(target.suffix + ".config.json")))
            self.assertEqual(expected_trace, log.read_text(encoding="utf-8"))
            self.assertEqual(expected_stdout, actual_stdout)
            self.assertEqual(manifest["output"], output)
            self.assertEqual(manifest["ticks"], ticks)


if __name__ == "__main__":
    unittest.main()
