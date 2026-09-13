# RFC-03 — Inline field echo (v0.3)

**Status:** Implemented in v0.3 (Python, TypeScript, Rust) behind an optional
flag. Additive and backward-compatible; the sid does not move.

**Author:** generated from live L1 comprehension analysis (2026-07).

## Problem

Live L1 (comprehension) results show PACT's token-dense rows are genuinely
harder for a model to *read* than keyed or field-labelled formats, at every
capability tier. On `claude-sonnet-4-6` (nq=40): JSON ≈ 0.90, **TOON ≈ 0.85,
PACT ≈ 0.35–0.45**. TOON differs from PACT mainly in one respect: it places the
field names on a header line *adjacent to the rows*, so the model does not have
to hold the schema (delivered far earlier in the prompt, or only as a `sid`)
in working memory while decoding positional values. The hypothesis is
**locality**: values are easier to interpret when their column names are next
to them.

PACT deliberately omits per-row keys — that density is the whole point (58–77%
fewer tokens than pretty JSON). But the schema is *already negotiated*; echoing
the field names **once per message** (not once per row, as JSON does) should
recover most of TOON's locality at a fixed ~15-token cost, independent of row
count. For a 200-row message that is ~0.07 tokens/row vs JSON's full key
repetition on every row.

## Design

Extend the body line with an optional field echo:

```
>tell s=extractor r=planner c=q sid=226909cf
walls[*]{id,level,fire_rating,thickness_mm,load_bearing,confidence}
W-001,L02,EI60,200,T,0.91
#n=1
```

Semantics:

1. **Pure rendering of the negotiated schema.** The echo lists the schema's
   field names in order. It is derived from the schema, adds no new
   information, and therefore **does not change canonicalization or the sid** —
   the sid hashes the *type contract*, not the message syntax. The TS
   sid-parity test stays green at `226909cf`; this RFC uses it as a tripwire in
   reverse (the sid must *not* move). The wire/codec `PROTOCOL_VERSION` bumps to
   `0.3`; `Schema.version` (the sid input) stays `0.2`.
2. **Verified fail-closed.** When an echo is present, the decoder checks it
   equals the schema's field names in order; a mismatch raises `PactError`.
   This is a new integrity check (E4 corruption class #8,
   `field_echo_mismatch`, at 100% detection on echo-bearing wires) — an echo
   that disagrees with the sid's schema is a corrupt message, not a hint to
   trust.
3. **Optional.** Both `name[N]` / `name[*]` (no echo) and `name[...]{fields}`
   decode. `encode(..., field_echo=True)` emits it; `session_prompt(...,
   field_echo=True)` instructs it; `gbnf_from_schema` emits it so a
   constrained decoder produces it and the echo is covered by grammar-level
   enforcement.

## Cost

~15 tokens per message (six field names for `walls`), fixed regardless of row
count — versus JSON, which repeats every key on every row. On any message with
more than a couple of rows the echo is strictly cheaper than keyed JSON while
buying back the locality that makes the rows readable.

## Invariants preserved (CLAUDE.md)

- sid **unchanged** (`226909cf`) — echo is message syntax, not schema identity.
- E6 grammar/decoder agreement stays 100/100 (grammar emits the echo; decoder
  verifies it).
- Landed in Python, TypeScript, and Rust in one change, with the TS
  sid-parity test and a new Rust `field_echo_v03` sid-parity assertion as the
  cross-language tripwires.

## What to measure (Phase 4)

An A/B with `--field-echo {on,off}` across three models on L1 (nq=40) and L3
(nhop=24, strict, v0.2 count profile), repeats=3, temperature=0. Prediction:
echo-on L1 approaches TOON's numbers at every tier while the message stays
denser than TOON (verified by an offline E1 rerun that includes the echo
variant). If confirmed, the field echo is the L1 fix and PACT keeps its token
lead with TOON-level readability.
