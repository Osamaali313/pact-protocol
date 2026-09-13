# KNOWLEDGE.md — research context for this project

## The one-paragraph thesis
Agent messages are dominated by structural boilerplate (keys, quotes, braces)
that is re-billed on every exchange, replies are unconstrained free text that
downstream agents must trust, and no protocol exploits prompt-cache economics
(cached prefix tokens ≈ 10× cheaper; agent workloads run at 100:1+
input:output ratios). PACT splits every exchange into a static, cache-resident
schema (negotiated once, content-addressed) and dynamic values-only messages,
and derives a decoding grammar from the schema so replies are valid by
construction.

## Corrected premises (do not reintroduce these errors)
- LLMs do NOT "understand assembly best." They are BPE-token predictors over a
  human-text-dominated corpus; raw formal syntax *hurts* (SR-LLM: −5.18%).
  Optimal representations are training-distribution-adjacent + tokenizer-dense.
- Host language (Rust/Go/C/asm) does not change token counts. The protocol is
  the optimization target.
- "Smaller than JSON" alone is solved (TOON, 2025: 30–60% savings). Novelty
  lives in the conjunction: schema-off-wire + cache-awareness + constraint
  transport + fail-closed semantics + protocol routing.

## Landscape map
| Line | Exemplars | What they solve | What they miss |
|---|---|---|---|
| Serialization | JSON, YAML, TOON | density (TOON) | no protocol, no enforcement, keys still on wire |
| Constrained decoding | LMQL, Outlines, SGLang | validity at user→model | no A2A transport of constraints |
| Agent protocols | MCP, A2A, ACP, ANP | transport, discovery, identity | semantic layer pushed to prompts (Yuan 2026) |
| A2A structure | TalkHier, LLM-X | envelope structure | content stays free-form |
| CQL (Tut 2026, internal) | — | names the enforcement gap | no payload language, no cost model |
| Latent exchange | C2C, Q-KVComm, KVCOMM, LCF | raw speed (≤6.7× prefill) | model-pair adapters, no audit, no interop |

PACT = intersection row: token-dense ∧ schema-off-wire ∧ decoder-enforced ∧
model-agnostic ∧ auditable ∧ routable.

## Verified claims (offline, reproducible: `experiments/run_benchmarks.py`)
- E1: 58–77% fewer tokens than formatted JSON; < TOON on all 4 workloads
  (exact GPT-2 BPE; tiktoken upgrade path built in).
- E3: lossless round-trip incl. comma/newline/backslash/null edge cases.
- E4: 100% detection of enum drift, range violation, arity drop, type swap,
  bool corruption, sid spoofing — vs 0% for plain JSON parsing.
- E5: codec ≤ ~1 ms per 200-row message; prefill dominates.
- E6: grammar and decoder accept exactly the same language.

## Projected claims (require live models — Phase P2; never state as measured)
- H3: accuracy gains in multi-hop pipelines from constrained replies.
  Supporting evidence exists (Zhu 2026: one structured confidence field
  improves MAD accuracy; compositionality gap ≈40%, Press 2022) but OUR number
  does not exist yet.

## Security posture (from CDA/CodeSpear line)
Never compile a grammar received raw from the wire; re-derive locally from the
co-negotiated sid. Lint enum/string terminals against safety policy.
Capability-scope which agents may `ask` which schemas. Fail closed always.

## Key references
LMQL 2212.06094 · Outlines 2307.09702 · SGLang 2312.07104 · TOON
github.com/toon-format/toon + 2603.03306 · MCP/A2A survey 2505.02279 ·
Semantic-layer critique 2604.02369 · TalkHier 2502.11098 · MAD+confidence
2601.19921 · CDA 2503.24191 · CodeSpear 2606.11817 · Logic-LM 2310.01179 ·
SR-LLM 2502.14253 · JSONSchemaBench 2502.18801 · C2C 2510.03215 · KVCOMM
2510.12872 · MLIR 2202.11133 · Tut (internal AI-03, 2026).
