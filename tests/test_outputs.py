"""Task-specific verifier for the Firmware Release Publisher assignment.

This file keeps the test package aligned with the actual reference solution.
All six functional criteria from scaffold_plan.yaml are exercised:

  1. report_output_matches                      — golden stdout after masking receipts
  2. withdrawals_and_duplicates_reconciled      — publishable bundle set excludes withdrawn builds
  3. bundles_signed_with_current_key_accepted   — all SIGNED lines reference the current key id
  4. receipts_and_tokens_persisted_in_duckdb    — publications table populated in releases.duckdb
  5. idempotent_rerun_no_duplicate_publications — byte-identical raw stdout across two runs
  6. revoked_key_signature_rejected             — (covered by gateway rejecting revoked-signed payloads;
                                                   the test verifies the solution never produces this path)
"""

from __future__ import annotations

import atexit
import re
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

import duckdb

if Path("/app").exists():
    REPO_ROOT = Path("/app")
else:
    REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_OUTPUT = (REPO_ROOT / "reports" / "publications.expected.txt").read_text(encoding="utf-8").strip()
GATEWAY_PROCESS: subprocess.Popen[str] | None = None


def ensure_gateway() -> None:
    global GATEWAY_PROCESS

    try:
        with urlopen("http://127.0.0.1:7070/healthz", timeout=0.5) as response:
            if response.status == 200:
                return
    except Exception:
        pass

    if GATEWAY_PROCESS is None or GATEWAY_PROCESS.poll() is not None:
        GATEWAY_PROCESS = subprocess.Popen(
            ["node", "server.js"],
            cwd=REPO_ROOT / "distribution-gateway",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    for _ in range(30):
        try:
            with urlopen("http://127.0.0.1:7070/healthz", timeout=0.5) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)

    if GATEWAY_PROCESS is not None and GATEWAY_PROCESS.poll() is None:
        GATEWAY_PROCESS.terminate()
        try:
            GATEWAY_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            GATEWAY_PROCESS.kill()
    raise RuntimeError("Gateway did not become ready")


def stop_gateway() -> None:
    global GATEWAY_PROCESS
    if GATEWAY_PROCESS is not None and GATEWAY_PROCESS.poll() is None:
        GATEWAY_PROCESS.terminate()
        try:
            GATEWAY_PROCESS.wait(timeout=5)
        except subprocess.TimeoutExpired:
            GATEWAY_PROCESS.kill()
        GATEWAY_PROCESS = None


atexit.register(stop_gateway)


def normalize_output(text: str) -> str:
    """Mask any RECEIPT=<value> token for diffing. The pattern matches any
    non-whitespace after RECEIPT= so both `pub_<hex>` and `pub-BND-NNN`
    golden placeholders are normalised identically."""
    text = text.strip()
    text = re.sub(r"RECEIPT=\S+", "RECEIPT=<receipt>", text)
    return text


def reset_publisher_state() -> None:
    """Delete releases.duckdb so a test starts from scratch. The gateway
    ledger is intentionally not reset — idempotency relies on the request
    tokens being stable even when the publisher's local DB is gone."""
    db_path = REPO_ROOT / "releases.duckdb"
    if db_path.exists():
        db_path.unlink()


def run_publisher(clean: bool = False) -> str:
    """Run the publisher under test via `npm run report`, which expands to
    `node publisher/release-publisher.mjs --report` from /app.  This is the
    same command the grader invokes; it exercises whichever code lives at
    /app/publisher/release-publisher.mjs — NOT a hardcoded solution path.

    Pass clean=True to blow away releases.duckdb before executing, forcing a
    fresh sign+submit cycle against the gateway."""
    ensure_gateway()
    if clean:
        reset_publisher_state()
    result = subprocess.run(
        ["npm", "run", "--silent", "report"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def extract_bundle_ids(output: str) -> set[str]:
    """Return the set of bundle_ids referenced by any SIGNED or PUBLISHED line."""
    ids: set[str] = set()
    for line in output.splitlines():
        m = re.match(r"BUNDLE (BND-\d+) ", line)
        if m:
            ids.add(m.group(1))
    return ids


# ---------------------------------------------------------------------------
# Criterion 1: report_output_matches
# ---------------------------------------------------------------------------

def test_solution_output_matches_golden_after_masking_receipts():
    """The solution must emit the expected publishable bundle lines with a
    deterministic structure and a masked receipt field that is stable across runs."""
    output = normalize_output(run_publisher(clean=True))
    expected = normalize_output(EXPECTED_OUTPUT)
    assert output == expected


# ---------------------------------------------------------------------------
# Criterion 2: withdrawals_and_duplicates_reconciled
# ---------------------------------------------------------------------------

def test_solution_reconciliation_fully_withdrawn_bundle_is_skipped():
    """Bundle BND-104 has every build withdrawn in the shipped fixture; the
    reconciled set must NOT include it."""
    output = run_publisher(clean=True)
    bundles = extract_bundle_ids(output)
    assert "BND-104" not in bundles, (
        f"BND-104 is fully withdrawn and must not appear in output. Found: {sorted(bundles)}"
    )


def test_solution_reconciliation_publishable_bundle_set_matches_fixture():
    """The three surviving bundles after reconciliation must be exactly
    {BND-101, BND-102, BND-103}."""
    output = run_publisher(clean=True)
    bundles = extract_bundle_ids(output)
    assert bundles == {"BND-101", "BND-102", "BND-103"}, (
        f"Publishable bundle set mismatch. Expected {{BND-101, BND-102, BND-103}}, got {sorted(bundles)}"
    )


def test_solution_output_ordered_by_bundle_id_ascending():
    """Publishable bundle lines must appear in ascending bundle_id order."""
    output = run_publisher(clean=True)
    signed_ids = [
        m.group(1)
        for line in output.splitlines()
        if (m := re.match(r"BUNDLE (BND-\d+) SIGNED ", line))
    ]
    assert signed_ids == sorted(signed_ids), f"SIGNED lines not ordered ASC: {signed_ids}"
    published_ids = [
        m.group(1)
        for line in output.splitlines()
        if (m := re.match(r"BUNDLE (BND-\d+) PUBLISHED ", line))
    ]
    assert published_ids == sorted(published_ids), f"PUBLISHED lines not ordered ASC: {published_ids}"


# ---------------------------------------------------------------------------
# Criterion 3: bundles_signed_with_current_key_accepted
# ---------------------------------------------------------------------------

def test_solution_uses_the_current_signing_key_only():
    """The signed bundle lines must point to the current trusted key rather
    than the revoked key."""
    output = run_publisher(clean=True)
    signing_lines = [line for line in output.splitlines() if line.startswith("BUNDLE ") and " SIGNED KEY=" in line]
    assert signing_lines, "No SIGNED lines emitted"
    assert all("KEY=fw-signing-2026-current" in line for line in signing_lines), (
        f"One or more SIGNED lines did not use the current key_id: {signing_lines}"
    )
    assert all("fw-signing-2025-revoked" not in line for line in signing_lines), (
        "Found revoked key 'fw-signing-2025-revoked' referenced in output"
    )


def test_solution_no_untrusted_signature_error_anywhere():
    """Zero submissions may be rejected as UNTRUSTED_SIGNATURE when the
    publisher signs with the current keypair."""
    output = run_publisher(clean=True)
    assert "UNTRUSTED_SIGNATURE" not in output, (
        "Output contains UNTRUSTED_SIGNATURE — publisher must not sign with the revoked key"
    )


# ---------------------------------------------------------------------------
# Criterion 4: receipts_and_tokens_persisted_in_duckdb
# ---------------------------------------------------------------------------

def test_solution_persists_receipts_and_tokens_in_duckdb():
    """After a fresh run releases.duckdb must exist, contain a publications
    table with one row per publishable bundle, and capture bundle_id,
    request_token, publication_id, status, and key_id."""
    run_publisher(clean=True)
    db_path = REPO_ROOT / "releases.duckdb"
    assert db_path.exists(), "releases.duckdb was not created after running the publisher"
    conn = duckdb.connect(str(db_path))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()]
        assert "publications" in tables, f"Expected 'publications' table, found tables: {tables}"
        rows = conn.execute(
            "SELECT bundle_id, request_token, publication_id, status, key_id "
            "FROM publications ORDER BY bundle_id"
        ).fetchall()
        assert len(rows) == 3, f"Expected 3 publication rows, got {len(rows)}: {rows}"
        bundle_ids = [r[0] for r in rows]
        assert bundle_ids == ["BND-101", "BND-102", "BND-103"], (
            f"Unexpected bundle ordering in DB: {bundle_ids}"
        )
        for bundle_id, request_token, publication_id, status, key_id in rows:
            assert request_token == f"token-{bundle_id}", (
                f"request_token for {bundle_id}: expected 'token-{bundle_id}', got '{request_token}'"
            )
            assert publication_id.startswith("pub_") or publication_id.startswith("pub-"), (
                f"publication_id for {bundle_id} has unexpected format: {publication_id!r}"
            )
            assert status == "PUBLISHED", f"status for {bundle_id}: expected PUBLISHED, got {status!r}"
            assert key_id == "fw-signing-2026-current", (
                f"key_id for {bundle_id}: expected fw-signing-2026-current, got {key_id!r}"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Criterion 5: idempotent_rerun_no_duplicate_publications
# ---------------------------------------------------------------------------

def test_solution_is_deterministic_raw_stdout_across_repeated_runs():
    """Running the publisher twice without state reset must produce
    byte-identical raw stdout (including the stored publication_ids)."""
    first = run_publisher(clean=True)
    second = run_publisher(clean=False)
    assert first == second, (
        "Raw stdout differs between run 1 and run 2 — idempotency broken.\n"
        f"--- RUN 1 ---\n{first}\n--- RUN 2 ---\n{second}"
    )


def test_solution_duckdb_row_count_stable_after_second_run():
    """A second run must not insert new rows into the publications table."""
    run_publisher(clean=True)
    db_path = REPO_ROOT / "releases.duckdb"
    conn = duckdb.connect(str(db_path))
    try:
        count_before = conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0]
    finally:
        conn.close()
    run_publisher(clean=False)
    conn = duckdb.connect(str(db_path))
    try:
        count_after = conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0]
    finally:
        conn.close()
    assert count_before == count_after == 3, (
        f"Publications row count changed across rerun: before={count_before}, after={count_after}"
    )

