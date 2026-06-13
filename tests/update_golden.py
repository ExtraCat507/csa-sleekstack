from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "golden"
sys.path.insert(0, str(ROOT))

from machine import run_cpu
from translator import AbstractSyntaxTree, MachineCode

CASES = [
    ("cat", "examples/cat.ss", "examples/cat.schedule"),
    ("double_precision", "examples/double_precision.ss", None),
    ("euler6", "examples/euler6.ss", None),
    ("features", "examples/features.ss", None),
    ("hello_user_name", "examples/hello_user_name.ss", "examples/hello_user_name.schedule"),
    ("hello", "examples/hello_world.ss", None),
    ("sort", "examples/sort.ss", "examples/sort.schedule"),
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(filename: Path) -> str:
    return sha256_bytes(filename.read_bytes())


def sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(payload)


def regenerate_case(name: str, source_name: str, schedule_name: str | None) -> None:
    case_dir = GOLDEN_DIR / name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)

    source = ROOT / source_name
    source_snapshot = case_dir / "source.ss"
    shutil.copy2(source, source_snapshot)

    schedule_snapshot = None
    if schedule_name is not None:
        schedule_snapshot = case_dir / "input.schedule"
        shutil.copy2(ROOT / schedule_name, schedule_snapshot)

    binary = case_dir / "program.bin"
    MachineCode(AbstractSyntaxTree(str(source_snapshot))).store(str(binary))

    generated_listing = binary.with_suffix(binary.suffix + ".lst")
    listing = case_dir / "program.lst"
    generated_listing.replace(listing)

    generated_config = binary.with_suffix(binary.suffix + ".config.json")
    config_file = case_dir / "config.json"
    generated_config.replace(config_file)

    trace = case_dir / "trace.log"
    output, ticks = run_cpu(
        str(binary),
        str(config_file),
        schedule_file=str(schedule_snapshot) if schedule_snapshot else None,
        log_file=str(trace),
    )

    stdout = case_dir / "stdout.txt"
    stdout.write_text(f"{output}\nticks: {ticks}\n", encoding="utf-8")

    config = json.loads(config_file.read_text(encoding="utf-8"))
    artifacts = {
        "source_sha256": sha256_file(source_snapshot),
        "machine_code_sha256": sha256_file(binary),
        "listing_sha256": sha256_file(listing),
        "config_sha256": sha256_json(config),
        "data_image_sha256": sha256_json(config["data_image"]),
        "trace_sha256": sha256_file(trace),
        "stdout_sha256": sha256_file(stdout),
    }
    if schedule_snapshot is not None:
        artifacts["schedule_sha256"] = sha256_file(schedule_snapshot)

    manifest = {
        "case": name,
        "source": "source.ss",
        "schedule": "input.schedule" if schedule_snapshot else None,
        "output": output,
        "ticks": ticks,
        "artifacts": artifacts,
    }
    (case_dir / "manifest.json").write_text(json.dumps(manifest, indent=4) + "\n", encoding="utf-8")


def main() -> int:
    for case in CASES:
        regenerate_case(*case)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
