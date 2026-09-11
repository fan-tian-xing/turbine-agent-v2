"""Run the project test suite and record Stage 7 validation evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "stage7" / "stage7_test_evidence.json"


def _git_head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def main() -> None:
    command = [sys.executable, "-m", "pytest", "-q"]
    result = subprocess.run(command, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": "src"}, text=True, capture_output=True)
    payload = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "stage7_test_evidence",
        "status": "passed" if result.returncode == 0 else "failed",
        "command": " ".join(command),
        "project_python": sys.executable,
        "commit": _git_head(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "returncode": result.returncode, "output": str(OUTPUT)}, ensure_ascii=False))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
