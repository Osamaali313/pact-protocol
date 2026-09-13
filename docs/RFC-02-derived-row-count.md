# RFC-02 — Derived row count (stop trusting the model's self-count)

**Status:** Draft / open for discussion. Not implemented. Per CLAUDE.md,
roadmap/wire-syntax changes land only after an accepted RFC.

**Author:** generated from live-benchmark failure analysis (2026-07).

## Problem

A PACT message declares its row count in the body line, `name[N]`, and then
emits N value rows. `decode` is fail-closed on the count: if the number of
value rows ≠ N, it raises `PactError("declared N rows, got M")`.

When a *human* or a *codec* writes the wire, N is always correct — it is
computed from the data (`encode` does exactly this). But when an **LLM
generates the wire over a plain API** (no grammar-constrained decoder), the
model has to state N *before or while* it streams the rows, and it cannot
reliably count its own output.

### Evidence (live benchmark, `results/live_cache.jsonl`)

Re-scoring the cached real responses (`experiments/diagnose.py`) found that
the single largest genuine PACT failure class is row-count drift, and **every
observed case is over-emission** — the rows themselves are well-formed and
schema-valid; only the declared count is too low:

```
declared 5,  got 6      declared 8,  got 10     declared 10, got 12
declared 8,  got 9      declared 6,  got 9      declared 14, got 16
declared 10, got 11     declared 12, got 17     declared 19, got 21
```

Because the payloads are otherwise valid, a lenient extractor that truncates
to N "recovers" them — but that silently discards real rows and defeats the
fail-closed guarantee (see `extract_wire(truncate=True)` and the extraction
delta in `LIVE_RESULTS.md`). Truncation is a measurement tool, not a fix.

## Proposal

Remove the model's obligation to self-count. Two candidate wire forms:

1. **No declared count (terminator-delimited).** The body line becomes
   `name` (no `[N]`), and the row list ends at the first blank line, an
   end-of-message sentinel (e.g. a lone `.`), or a `^`-provenance line. The
   decoder counts the rows it actually read; there is nothing to disagree
   with. Simplest for generation; the count is *derived*.

2. **Trailing count (checksum, not gate).** Keep `name` on the body line and
   append the count as the last line, `#=<M>`, emitted *after* the rows when
   the model already knows M. Decode derives the count from the rows and uses
   `#=M` only as an optional integrity check (mismatch = warn or fail per a
   flag), analogous to a length trailer.

Either way, generation no longer requires foresight the model lacks, while
codec- and human-authored wires stay exactly as safe.

## Fail-closed is preserved

This does **not** weaken fail-closed decoding. Arity (per-row field count),
enum, range, bool-literal, and sid checks are unchanged and still raise on any
violation. Only the *self-referential count gate* — the one check a correct
payload can fail purely by miscounting — is replaced by a derived value. A
truncated or genuinely short payload still fails every per-row check it should.

## Cost / blast radius (why this is an RFC, not a patch)

Per CLAUDE.md this is a **wire-syntax change**, so it:

- bumps the schema canonicalization → **every `sid` changes** (content
  addressing working as intended) → update all fixtures;
- must land in **`src/pact` (Python), `sdk-ts/`, and `rust/` in one commit**,
  with matching updates to `gbnf_from_schema` and `row_validators`;
- must keep **E6 at 100/100** (grammar/decoder agreement) and the TS
  sid-parity test green (the cross-language tripwire).

The GBNF grammar simplifies under option 1 (drop the `count` rule; `body ::=
row ("\n" row)*` already needs no count), which is a point in its favor for
true grammar-level enforcement on vLLM/llama.cpp.

## Open questions

- Terminator choice for option 1 (blank line vs explicit sentinel) and its
  interaction with multi-message streams on one connection.
- Whether the trailing checksum (option 2) is worth keeping any count at all,
  given grammar-constrained decoders make miscounts impossible anyway.
- Migration: dual-read (accept both `name` and `name[N]`) during a
  deprecation window, or a hard cutover with a version bump in `Schema`.

## Recommendation

Prototype **option 1** behind the existing benchmark so the accuracy delta vs
today's `name[N]` form is measured on live models before touching the wire in
all three languages.
