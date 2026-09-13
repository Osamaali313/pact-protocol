# Contributing to PACT

First off — thank you. PACT is a protocol, and protocols win by adoption,
scrutiny, and careful evolution. Whether you're fixing a typo, porting the codec
to a new language, or proposing a wire-syntax change, this guide tells you how to
do it in a way that keeps the protocol coherent across every implementation.

- [Ways to contribute](#ways-to-contribute)
- [Code of conduct](#code-of-conduct)
- [The non-negotiable invariants](#the-non-negotiable-invariants)
- [Development setup](#development-setup)
- [Running the tests](#running-the-tests)
- [Changing the wire syntax (read this first)](#changing-the-wire-syntax-read-this-first)
- [The RFC process](#the-rfc-process)
- [Coding style](#coding-style)
- [Commit & pull-request conventions](#commit--pull-request-conventions)
- [Pull-request checklist](#pull-request-checklist)
- [Reporting security issues](#reporting-security-issues)

---

## Ways to contribute

You don't need to touch the wire format to help. High-value contributions:

| Area | Examples |
|---|---|
| 🐛 **Bug reports** | A `decode()` that should raise but doesn't, a parity mismatch between languages, a wrong number |
| 📚 **Docs** | Clarify `docs/ARCHITECTURE.md`, improve examples, fix the README, write a tutorial |
| 🔌 **Integrations** | A new provider adapter under `integrations/`, a framework binding |
| 🧪 **Tests** | Especially Python unit tests (currently guarded mainly by the benchmark + parity suites), fuzzing, edge cases |
| 🌍 **Language ports** | A codec in Go, Java, C#, … that stays `sid`-compatible |
| 📈 **Experiments** | New workloads, additional models in the live suite, better figures |
| 📐 **Spec / RFCs** | Nesting dialect (RFC-01), grammar-enforcement harnesses, new corruption classes |

If you're planning something non-trivial, **open an issue first** so we can align
before you write code.

---

## Code of conduct

Be respectful, assume good faith, and keep discussion technical. Harassment,
personal attacks, or discriminatory language are not welcome. We follow the
spirit of the [Contributor Covenant](https://www.contributor-covenant.org/).
Maintainers may remove comments, commits, or contributors that violate this.

---

## The non-negotiable invariants

PACT's guarantees only hold if every change respects these. A PR that breaks one
will be asked to change, no matter how good it otherwise is. (Full context in
[`CLAUDE.md`](CLAUDE.md).)

1. **Fail-closed is a protocol invariant.** `decode` MUST raise `PactError` (or
   the language's equivalent) on *any* violation — sid, arity, enum, range,
   bool/number literal, row count, field-echo mismatch. **Never** add a
   tolerant or "repair" parsing path. A silent recovery is a bug, not a feature.

2. **Grammar and decoder must agree.** Any change to wire syntax requires a
   matching change in `gbnf_from_schema` **and** `row_validators`, and
   experiment **E6 must stay at 100/100** (every valid row accepted, every
   corrupt row rejected).

3. **The core stays dependency-free.** `src/pact` and the published SDKs import
   nothing at runtime. Benchmark-only dependencies go in the `bench` extra
   (`pyproject.toml`), never in the core.

4. **Cross-language parity is enforced.** `src/pact` (Python), `sdk-ts/`
   (TypeScript), and `rust/` must stay wire- and `sid`-compatible. A
   canonicalization or syntax change lands in **all three plus GBNF synthesis in
   the same commit**. The TypeScript suite's `sid`-parity test is the tripwire.

5. **Never hand-edit measured numbers.** Every figure/table in the paper and docs
   derives from `results/results.json`. Change code → rerun
   `experiments/run_benchmarks.py` → rerun `experiments/make_figures.py`. Only
   claims measured by E1–E6 (or the live suite) may be stated as fact; anything
   else is labeled *projected*.

6. **Content addressing is intentional.** A wire-syntax change bumps the schema
   canonicalization, which changes every `sid`. That's expected — update the
   fixtures; don't work around it.

---

## Development setup

Clone your fork, then set up whichever implementation you're touching.

### 🐍 Python (core)

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt         # bench + live extras; core needs nothing
python -m pip install -e .                         # editable install of the `pact` package
```

### 🟦 TypeScript (`sdk-ts/`)

```bash
cd sdk-ts && npm install
```

### 🦀 Rust (`rust/`)

```bash
cd rust && cargo build
```

---

## Running the tests

Run the suite(s) for whatever you changed. If you touched the wire format, run
**all three** plus the offline benchmark.

```bash
# TypeScript — 11/11, includes the sid-parity tripwire (must print sid 226909cf)
cd sdk-ts && npm test

# Rust — 5/5 round-trip + sid-parity
cd rust && cargo test

# Offline protocol suite E1–E6 (no keys, no network) — E6 MUST be 100/100
python experiments/run_benchmarks.py

# Python unit tests, once a tests/ dir exists
python -m pytest tests/
```

A green PR is one where every relevant suite passes **and** the three languages
still agree on `sid` and on byte-exact round-trips.

---

## Changing the wire syntax (read this first)

This is the one change with real blast radius. If your PR alters how bytes look
on the wire (a new profile, a new terminal, a different separator, …), it must,
**in a single commit**:

1. Update the encoder/decoder in **Python** (`src/pact/__init__.py`).
2. Mirror it in **TypeScript** (`sdk-ts/src/index.ts`).
3. Mirror it in **Rust** (`rust/src/lib.rs`).
4. Update **GBNF synthesis** (`gbnf_from_schema`) **and** `row_validators` so the
   grammar and the parser accept exactly the same language (E6 = 100/100).
5. Regenerate every affected `sid` and update all fixtures/tests (expect the
   `sid`-parity constant to change across all three languages together).
6. Add or extend an E4 corruption class if the change introduces a new way a
   message could be malformed — and keep detection at 100%.
7. Rerun `experiments/run_benchmarks.py` + `make_figures.py`; commit no
   hand-edited numbers.

If that sounds like a lot — it is, on purpose. Wire changes should go through an
RFC first.

---

## The RFC process

For anything that changes the **spec, wire syntax, or a protocol guarantee**
(not for bug fixes or docs):

1. Open an issue titled `RFC: <short title>` describing the problem, the proposed
   change, the blast radius (which files/languages/`sid`s), and alternatives.
2. Discuss. Maintainers and the community weigh in.
3. On rough consensus, add a `docs/RFC-NN-*.md` following the style of
   [`RFC-02`](docs/RFC-02-derived-row-count.md) and
   [`RFC-03`](docs/RFC-03-field-echo.md).
4. Implement it across all three languages per the checklist above.

Roadmap items already flagged for RFCs: the **nesting dialect** (progressive
lowering for non-uniform payloads) and the **vLLM/Outlines live-model harness**
for grammar-level L3 enforcement.

---

## Coding style

- **Python** ≥ 3.10, type hints, standard library only in the core, no `print`
  in library code. Keep it readable and match the surrounding style.
- **TypeScript** strict mode, zero runtime dependencies, ES modules.
- **Rust** idiomatic, `cargo fmt` clean.
- Match the comment density and naming of the code around you. Small, focused
  diffs review faster than sprawling ones.

---

## Commit & pull-request conventions

- Write clear, imperative commit subjects: `fix: reject arity drift in model profile`.
- Keep unrelated changes in separate commits/PRs.
- Reference the issue or RFC your PR addresses (`Closes #12`).
- **Sign off your commits** to certify the [Developer Certificate of
  Origin](https://developercertificate.org/):

  ```bash
  git commit -s -m "your message"
  ```

  This appends a `Signed-off-by:` line asserting you have the right to submit the
  code under the project's Apache-2.0 license.

---

## Pull-request checklist

Before you open a PR, confirm:

- [ ] Tests pass for every language you touched (`npm test`, `cargo test`, `run_benchmarks.py`).
- [ ] If the wire changed, **all three** implementations + GBNF changed in this PR, and `sid` fixtures are updated.
- [ ] **E6 is 100/100** and no new E4 corruption class regressed below 100%.
- [ ] `decode` still fails closed — no tolerant/repair path was introduced.
- [ ] The core gained no runtime dependencies.
- [ ] No measured numbers were hand-edited; results were regenerated from code.
- [ ] Docs/README updated if behavior or the public API changed.
- [ ] Commits are signed off (`-s`).

---

## Reporting security issues

Constrained decoding is itself an attack surface (see the Security Considerations
in the paper). **Do not open a public issue for a vulnerability.** Instead, use
GitHub's private reporting: **Security → Report a vulnerability** on the
repository, which opens a private advisory with the maintainers. Please include a
reproduction and the affected `sid`/profile if relevant. We'll acknowledge and
coordinate a fix and disclosure with you.

---

Thanks again for contributing. Negotiate the schema once, ship values, enforce
the reply, fail closed — and let's make agent communication provable together. ⚡
