# PACT — Prompt-cache-Aware, Constrained, Typed protocol

A text-based, model-agnostic wire protocol for agent-to-agent (A2A) and
human-to-agent (H2A) communication. Three mechanisms:

1. **Schema negotiation (L1)** — types, enums, and ranges exchanged once per
   session, content-addressed (`sid = sha256`), pinned in the prompt-cached
   prefix of both agents (cached tokens ≈ 10× cheaper than fresh).
2. **Token-dense wire encoding (L2)** — values-only tabular rows; no repeated
   keys, quotes, or braces. Measured **49–71% fewer tokens than formatted
   JSON** and consistently at or below TOON (exact o200k_base counts).
3. **Constrained generation contract (L3)** — a GBNF grammar auto-derived from
   the schema constrains the responder's decoder: replies are
   **valid-by-construction**, and decoding is **fail-closed** (100% detection
   across eight corruption classes vs 0% for plain JSON parsing).

Companion artifacts: research paper (DOCX), research proposal (PDF), slide
deck (PPTX), and this repository.

## Repository layout

```
src/pact/            core library: schema, wire codec, grammar synthesis
src/pact/bench_support.py   exact GPT-2 BPE tokenizer + baseline serializers
experiments/
  run_benchmarks.py  E1–E6 experiment suite (fully offline, reproducible)
  make_figures.py    publication figures from results.json
  assets/            vendored GPT-2 vocab (encoder.json, vocab.bpe)
rust/                reference Rust encoder/decoder crate (`pact-wire`)
results/             results.json · RESULTS.md · figures/*.png
docs/                ARCHITECTURE / KNOWLEDGE / WHY_NOW / FAQ / PUBLISHING
CLAUDE.md            instructions for coding agents working in this repo
```

## Live benchmark — unzip, add keys, run (L1–L3)

The offline suite below needs no keys. The LIVE suite runs the same protocol
against real models through your own subscriptions:

```bash
pip install -r requirements.txt
cp .env.example .env            # paste whichever API keys you have
python3 experiments/live_bench.py --dry-run    # shows planned calls, costs $0
python3 experiments/live_bench.py              # runs every provider it finds
```

Auto-detected from .env: Anthropic, OpenAI, OpenRouter, Groq, DeepSeek, xAI,
MiniMax (override with `--models "anthropic:claude-sonnet-4-6,..."`; model IDs
are July-2026 defaults — adjust to your account). Dataset ships pre-generated
in `experiments/data/` (1,000 walls + 600 tasks + 800 logs + QA with computed
ground truth; regenerate identically with `python3 experiments/data_gen.py`).

Per model it measures, for JSON vs TOON vs PACT:
**L1** comprehension accuracy on data QA · **L2** generation validity
(first-try, after fail-closed retries, content-exact) · **L3** two-hop
end-task accuracy across an agent hand-off (the H3 mechanism) · plus real
input/output/cached token usage from provider billing fields.

Output: `results/live_results.json` + `results/LIVE_RESULTS.md` — the tables
slot directly into paper §5.7. Responses are cached in
`results/live_cache.jsonl`, so interrupted or repeated runs never re-bill.
Sanity-check the pipeline offline anytime: `--models mock:pipeline`
(validates plumbing only; its numbers are meaningless).

**Diagnosing a run:** `python3 experiments/diagnose.py` reads your existing
`results/live_cache.jsonl` (zero API calls) and classifies every PACT failure
as mechanical (markdown fences, true/false-vs-T/F, row-count drift — several
of which the current extractor now auto-recovers) or genuine. After pulling a
new harness version, KEEP your cache file and re-run: cached responses are
re-scored for free; only new prompts (e.g. the v2 one-shot session prompt,
or the L3 `PACT-decoded` condition) get billed.

## Reproduce every number in the paper (offline, no keys)

```bash
python3 -m pip install -r requirements.txt
python3 experiments/run_benchmarks.py     # writes results/results.json + RESULTS.md
python3 experiments/make_figures.py       # writes results/figures/*.png
```

No network, no API keys, no GPUs required. Token counts use an exact,
vendored GPT-2 byte-level BPE; if `tiktoken` can fetch its files, the harness
automatically upgrades to `o200k_base`. Relative rankings between formats are
stable across tokenizers (see paper §5.1).

Experiments:

| ID | Question | Verdict (this machine) |
|---|---|---|
| E1 | Tokens/message: PACT vs JSON/YAML/XML/TOON | −58% to −77% vs pretty JSON; beats TOON on all 4 workloads |
| E2 | Session cost with cache pricing | see `results/figures/fig2_session_cost.png` |
| E3 | Lossless round-trip incl. escaping edge cases | 5/5 |
| E4 | Corruption detection (6 classes × 200 trials) | PACT 100% / JSON 0% |
| E5 | Codec throughput | ≤ ~1 ms per 200-row message (not the bottleneck) |
| E6 | Grammar/decoder agreement | 100% valid accepted, 100% corrupt rejected |

**Not measured here (requires live models — Phase P2):** end-task accuracy
gains from constrained replies (hypothesis H3). Marked *projected* wherever it
appears in the paper.

## Library use

```python
from pact import Schema, Field, encode, decode, gbnf_from_schema

walls = Schema("walls", (
    Field("id", "str"),
    Field("level", "enum", ("L01", "L02", "L03", "L04", "ROOF")),
    Field("fire_rating", "enum", ("EI30", "EI60", "EI90", "EI120", "NR")),
    Field("thickness_mm", "int", lo=50, hi=600),
    Field("load_bearing", "bool"),
    Field("confidence", "float", lo=0.0, hi=1.0),
))
wire = encode(records, walls, sender="extractor", receiver="planner")
msg = decode(wire, {walls.sid: walls})          # fail-closed
grammar = gbnf_from_schema(walls)               # feed to llama.cpp / Outlines
```

## TypeScript SDK (`sdk-ts/` — npm-ready, tested)

```bash
cd sdk-ts && npm install && npm test    # 9/9: sid parity with Python (226909cf),
                                        # round-trip, 6 fail-closed classes,
                                        # mocked-provider retry loop
```

```ts
import { Schema, encode, decode, gbnfFromSchema, askPact, sessionPrompt } from "pact-wire";
```

Cross-language parity is tested: Python-encoded wire decodes byte-exactly in
TypeScript, and both compute identical schema sids.

## Provider integrations (`integrations/`)

One pattern covers every major SDK — see `integrations/INTEGRATIONS.md`:
Anthropic (cache_control-pinned schema), OpenAI, and every OpenAI-compatible
endpoint (OpenRouter, Groq, DeepSeek, MiniMax, xAI) via one file with a
`base_url` switch, Vercel AI SDK, and true grammar-level enforcement on
self-hosted vLLM/llama.cpp. Users bring their own API keys; PACT adds no
service or proxy.

## Rust reference implementation

```bash
cd rust && cargo test && cargo run --release   # encoder/decoder + round-trip test
```

The Rust crate exists for embedding in high-throughput gateways; note that the
host language does **not** change token counts — the protocol is the
optimization, not the implementation language.

## Publishing the library (recommended path)

1. **PyPI** `pact-protocol` (this package; `python -m build && twine upload`).
2. **npm** `pact-wire` (sdk-ts/ in this repo, tests passing; `npm publish` when ready).
3. **crates.io** `pact-wire` for gateway/proxy embedding.
4. Spec as versioned Markdown in-repo; changes via RFC issues (MCP playbook).

## License

Apache-2.0 (proposed) — protocol specs win by adoption; permissive or dead.
