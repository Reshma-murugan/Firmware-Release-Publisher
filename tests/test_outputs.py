"""Task-specific verifier for the Firmware Release Publisher assignment.

This file keeps the test package aligned with the actual reference solution:

- the publisher must run from the repository root,
- it must emit deterministic bundle status lines,
- it must sign with the current key, and
- repeated runs must be byte-identical after masking the random receipt IDs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OUTPUT = (REPO_ROOT / "environment" / "reports" / "publications.expected.txt").read_text(encoding="utf-8").strip()


def normalize_output(text: str) -> str:
    text = text.strip()
    text = re.sub(r"RECEIPT=pub_[A-Za-z0-9]+", "RECEIPT=<receipt>", text)
    return text


def run_publisher() -> str:
    result = subprocess.run(
        ["node", "../solution/publisher/release-publisher.mjs", "--report"],
        cwd=REPO_ROOT / "environment",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_solution_output_matches_golden_after_masking_receipts():
    """The solution must emit the expected publishable bundle lines with a
    deterministic structure and a masked receipt field that is stable across runs."""
    output = normalize_output(run_publisher())
    expected = normalize_output(EXPECTED_OUTPUT)
    assert output == expected


def test_solution_is_deterministic_across_repeated_runs():
    """Running the publisher twice must produce byte-identical output."""
    first = normalize_output(run_publisher())
    second = normalize_output(run_publisher())
    assert first == second


def test_solution_uses_the_current_signing_key_only():
    """The signed bundle lines must point to the current trusted key rather
    than the revoked key."""
    output = run_publisher()
    signing_lines = [line for line in output.splitlines() if line.startswith("BUNDLE ") and " SIGNED KEY=" in line]
    assert signing_lines
    assert all("KEY=fw-signing-2026-current" in line for line in signing_lines)
    assert all("fw-signing-2025-revoked" not in line for line in signing_lines)
