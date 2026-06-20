"""Offline validation gate: unit tests and optional retrieval smoke test.

Usage (from repo root, with `.venv` active):

    .\\.venv\\Scripts\\python.exe -m backend.scripts.run_validation

With optional retrieval / ML warm load (~minutes on cold cache):

    $env:CUSTOMSAI_VALIDATE_RETRIEVAL=\"1\"; .\\.venv\\Scripts\\python.exe -m backend.scripts.run_validation
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _root() -> str:
    # .../backend/scripts/run_validation.py -> repo root (.../customsAI)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _run_tests() -> None:
    root = _root()
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            os.path.join(root, "backend", "tests"),
            "-v",
        ],
        cwd=root,
    )


def _run_retrieval_smoke() -> None:
    root = _root()
    subprocess.check_call(
        [sys.executable, "-m", "backend.scripts.verify_head_noun"],
        cwd=root,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Run CustomsAI offline validation.")
    p.add_argument(
        "--smoke-retrieval",
        action="store_true",
        help="Run verify_head_noun retrieval script (downloads models on first run). "
        "Also enabled by CUSTOMSAI_VALIDATE_RETRIEVAL=1.",
    )
    args = p.parse_args()
    retrieval = args.smoke_retrieval or (
        os.environ.get("CUSTOMSAI_VALIDATE_RETRIEVAL", "").strip().lower() in ("1", "true", "yes")
    )

    print("[validate] unittest discover backend/tests", flush=True)
    _run_tests()
    print("[validate] OK - all unit tests passed", flush=True)

    if retrieval:
        print("[validate] optional retrieval smoke (verify_head_noun)", flush=True)
        _run_retrieval_smoke()
        print("[validate] OK - retrieval smoke finished", flush=True)


if __name__ == "__main__":
    main()
