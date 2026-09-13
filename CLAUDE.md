# CLAUDE.md — instructions for coding agents in this repository

## What this repo is
PACT: a prompt-cache-aware, constrained, typed wire protocol for A2A/H2A
communication. Python core (`src/pact`), Rust reference codec (`rust/`),
offline experiment suite (`experiments/`), paper artifacts generated from
`results/results.json`.

## Ground rules
- **Never edit numbers in the paper/docs by hand.** Every figure and table is
  derived from `results/results.json`. Change code → rerun
  `experiments/run_benchmarks.py` → rerun `experiments/make_figures.py` →
  regenerate documents.
- **Fail-closed is a protocol invariant.** `pact.decode` must raise
  `PactError` on ANY violation (sid, arity, enum, range, bool/number
  literals, row count). Do not add tolerant/repair parsing paths.
- **Grammar and decoder must agree.** Any change to wire syntax requires a
  matching change in `gbnf_from_schema` and `row_validators`, and E6 must
  stay at 100/100.
- **Wire syntax changes bump the schema canonicalization**, which changes
  every `sid`. That is intended (content addressing) — update fixtures.
- Keep the core dependency-free. Benchmark-only deps go in the `bench` extra.

## Commands
- `python3 experiments/run_benchmarks.py` — full E1–E6 suite (offline)
- `python3 experiments/make_figures.py`   — figures
- `cd rust && cargo test`                 — Rust codec round-trip tests
- `python3 -m pytest tests/` (when added) — unit tests

## Style
- Python ≥3.10, stdlib-only core, type hints, no prints in library code.
- Honest claims only: anything not measured by E1–E6 is labeled *projected*.

## Cross-language invariant
`src/pact` (Python), `sdk-ts/` (TypeScript), and `rust/` must stay wire- and
sid-compatible. Any canonicalization or syntax change lands in all three plus
`gbnf` synthesis in the same commit; the TS suite's sid-parity test is the
tripwire.

## Roadmap hooks (do not silently implement — open an RFC)
- Nesting dialect (progressive lowering) for non-uniform payloads
- vLLM/Outlines live-model harness for H3 accuracy experiments (P2)
