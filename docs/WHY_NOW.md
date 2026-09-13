# WHY NOW — impact due diligence, user stories, and honest sizing

## Why now (three curves crossing)

1. **Agent token economics inverted.** Production agent loops run at 100:1 to
   164:1 input:output ratios; tool schemas and structural boilerplate dominate
   prefill. Input tokens are now the cost center of agentic AI.
2. **Prompt caching became universal and ~10× cheaper** across major providers
   (2024–2026). Every protocol designed before this repriced the problem is
   leaving the discount on the table — JSON payloads actively poison cache
   prefixes by interleaving static structure with dynamic values.
3. **Constrained decoding became free** (FSM vocabulary indexing, Outlines
   2023; native structured-output endpoints, 2024–2025) — but only at the
   user→model boundary. The A2A boundary, where multi-agent systems actually
   fail, has no enforcement mechanism (the CQL gap, Tut 2026).

A fourth forcing function: **interoperability regulation and audit.** Latent
KV-exchange (the speed-maximal alternative) produces no human-readable
transcript. Any regulated deployment (construction compliance, finance,
health) requires an auditable wire — text protocols are not a transitional
technology there; they are the requirement.

## Impact due diligence (what changes if PACT works)

**Verified today (E1–E6):**
- 49–71% fewer payload tokens than formatted JSON at equal information — at
  fleet scale this is direct COGS reduction on the dominant cost line.
- 100% detection of eight corruption/drift classes that plain JSON passes
  silently — silent inter-agent corruption is today's dominant, undebugged
  failure mode.
- Deterministic, provenance-carrying transcripts — the audit substrate that
  regulated agent deployments currently lack.

**Projected (P2, honest status: unproven):**
- Accuracy gains at compositional hand-offs (H3). If this lands, PACT is a
  correctness contribution, not only a cost one. If it does not, PACT remains
  a cost+integrity contribution — still deployable, smaller paper.

**Who captures the value:**
- Agent platform teams (framework-level adoption → default win, MCP playbook)
- Regulated-industry deployments (audit + fail-closed as compliance features)
- Panovia/Attimo: first production proof on AEC extraction workloads (IFC →
  typed records with provenance is literally the Holter Tower pipeline)

**Realistic ceiling / failure modes:**
- TOON's community momentum could absorb the schema-off-wire idea faster than
  we publish → mitigation: ship the spec + SDK early, publish the benchmark.
- Providers could make caching so aggressive that JSON's overhead stops
  mattering → partially true at best; enforcement and fail-closed value stand
  regardless of pricing.
- Latent exchange wins inside single-vendor fleets → conceded in-paper; PACT's
  turf is heterogeneous, cross-org, auditable communication.
- This is a protocols/systems contribution, not an architecture breakthrough.
  Its ceiling is "TCP/IP of agent payloads," reached by adoption, not citations.

## User stories

**US-1 · Multi-agent extraction (Panovia).** As the planner agent, I ask the
extractor for the fire-rated walls on L02. Today I get prose I must re-parse
and trust. With PACT I send `>ask` under sid=226909cf and receive rows that
*cannot* contain a rating outside {EI30…NR}, each stamped with its IFC entity
provenance.

**US-2 · Fleet cost owner.** As the platform lead running 40M agent messages
a month, moving planner↔extractor↔verifier traffic to PACT cuts payload
prefill 49–71% and moves schemas into the cached prefix; the CFO sees the
input-token line drop without any model change.

**US-3 · Compliance officer.** As the auditor of an AI-assisted building
permit workflow, I can replay every inter-agent exchange as human-readable,
schema-bound, provenance-stamped rows — and every out-of-range value was
rejected at the wire, with a log entry, not silently absorbed.

**US-4 · Human operator.** As a project manager, I ask in plain English; the
bridge translates to PACT, agents exchange constrained messages, and my answer
comes back with citations to the exact drawing sheets — the Logic-LM division
of labor: models translate, machinery enforces.

**US-5 · Framework maintainer.** As a LangGraph/CrewAI-class maintainer, I
adopt PACT as an inter-node payload option: one dependency-free codec, one
grammar hook into my existing structured-output path, zero model retraining.

## The pitch in one line
> Stop paying full price to re-send the same structure ten thousand times a
> day, and stop trusting free-text replies at machine-to-machine boundaries:
> negotiate the schema once, cache it, ship values, enforce the reply.
