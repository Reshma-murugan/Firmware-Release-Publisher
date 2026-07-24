# Author Notes

This repository package is a task-authoring scaffold for the Firmware Release Publisher assessment.

## What is being delivered
- A precise task brief in `instruction.md`
- A working reference solution under `solution/publisher/release-publisher.mjs`
- A solution entrypoint in `solution/publish.sh`
- A local verification flow that demonstrates the expected behavior in a clean container

## Verification summary
The verification flow is completed in the following six steps:
0. Orient the repository and environment by reading the task brief and starting the provided gateway workflow.
1. Build or start the provided container environment from `environment/Dockerfile`.
2. Reconcile the manifest in DuckDB and confirm the publishable bundle rows are derived correctly.
3. Prove signing works in isolation by producing and validating a canonical descriptor signature.
4. Wire the publication loop, store publication receipts, and confirm the idempotent second-run behavior.
5. Verify that the final emitted report is deterministic and matches `reports/publications.expected.txt` exactly.

