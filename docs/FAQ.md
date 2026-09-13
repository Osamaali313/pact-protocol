# FAQ — anticipated questions (reviewers, skeptics, adopters)

**Q1. Isn't this just TOON with extra steps?**
No — and the benchmark shows it directly. TOON is a serialization format:
keys and structure still travel with every message, there is no session, no
routing, no schema binding, and no enforcement. PACT moves keys/types off the
wire entirely (into the ~10×-cheaper cached prefix), binds every message to a
content-addressed schema, and derives a decoding grammar so replies are valid
by construction. Empirically PACT is denser than TOON on all four workloads
(E1) *while carrying* routing metadata TOON doesn't have, and TOON's own
generation-side benchmark (Matveev 2026) shows the in-context-learning
overhead PACT eliminates structurally.

**Q2. Models were never trained on PACT syntax. Won't comprehension suffer?**
Two answers. Input side: PACT's surface is deliberately CSV/YAML-adjacent —
the highest-frequency tabular patterns in training corpora — and TOON's
four-model benchmark already shows tabular value streams score *higher*
comprehension than JSON. Output side: the model doesn't need to have learned
the syntax at all, because the decoder is constrained to it; that's the point
of L3. This is the planned A/B in P2, reported either way.

**Q3. Why not just use JSON + JSON Schema + structured outputs?**
That stack validates one hop at the user→model boundary and still re-bills the
full structural boilerplate every message. JSONSchemaBench (Geng 2025) shows
schema compliance fails on non-trivial constraints without hard decoding; and
JSON Schema on the wire is exactly the static content that should live in the
cached prefix instead. PACT is that stack, restructured around cache economics
and extended across the A2A boundary.

**Q4. CFGs can't check `50 ≤ x ≤ 600`. Isn't 'valid by construction' oversold?**
Correct, and the paper says so: the grammar enforces type shape and closed
enums; numeric ranges enforce at parse time — still fail-closed (E4: 100%
detection), just one layer later. We claim valid-by-construction for the
grammar-expressible fragment and fail-closed for the rest.

**Q5. Latent/KV-cache exchange (C2C, Q-KVComm) is faster. Why bother with text?**
Conceded for same-vendor fleets. Latent exchange needs trained adapters per
model pair (up to ~1 GB), breaks on heterogeneous vendors, produces no
auditable transcript, and has an emerging attack literature (LCGuard). PACT is
the frontier point for heterogeneous, cross-organization, regulated
deployments — the setting MCP/A2A exist for.

**Q6. Nested and non-uniform data?**
Known limitation, stated in-paper. The v0.1 core is deliberately the
uniform-record fragment, which dominates agent traffic (tool results, task
lists, extraction outputs, logs). The nesting dialect follows MLIR-style
progressive lowering (RFC-01); until then, non-uniform payloads may fall back
to JSON inside a PACT envelope — routing, sid binding, and fail-closed
semantics still apply.

**Q7. Where are the accuracy numbers?**
E1–E6 are measured and reproducible offline in this repo. H3 (end-task
accuracy from constrained replies) requires live models and is explicitly
labeled *projected*, with the P2 protocol pre-registered in the paper. We will
not launder a projection into a result.

**Q8. Doesn't the schema header cost tokens too?**
~60–90 tokens, paid fresh once, then read at cache price for the rest of the
session. Break-even against JSON occurs within the first message; against
TOON within the first two on our workloads (E2 curve).

**Q9. What about the CDA / CodeSpear attacks on constrained decoding?**
First-class design constraint, not an afterthought: grammars are never
compiled from wire input — the responder re-derives them locally from the
co-negotiated sid; enum/string terminals are linted against safety policy;
`ask` rights are capability-scoped per schema. Section 7 of the paper.

**Q10. Why should anyone adopt a new wire format? JSON won for a reason.**
JSON won a human-developer ergonomics war. The billing unit changed: tokens,
not bytes, and cached vs fresh. Adoption strategy is the MCP playbook —
permissive license, spec + SDKs (Python/TypeScript/Rust), one flagship
production deployment (Panovia AEC pipelines), and drop-in envelope
compatibility with MCP/A2A so nobody replaces their transport.

**Q11. Is this a Nobel/Turing-level result?**
No, and claiming so would sink it in review. It is a systems/protocol
contribution with measured cost and integrity wins and one high-upside open
hypothesis. Protocols matter through adoption; that is the ambition and the
plan.

**Q12. Who is the first customer?**
Panovia's multi-agent AEC pipeline: IFC/drawing extraction into typed,
provenance-stamped records under compliance constraints — the exact workload
PACT's demo schema encodes.

**Q13. What would falsify the thesis?**
(a) Live-model comprehension of PACT payloads materially below JSON after the
schema is in-prefix; (b) no measurable H3 accuracy delta at compositional
hand-offs; (c) provider pricing changes that erase the cached/fresh spread.
(a) and (b) are exactly what P2 tests; (c) leaves integrity/audit value intact.

**Q14. Why 5 performatives when FIPA-ACL had ~20?**
The classical set encoded unverifiable mental states (the Labrou critique).
PACT keeps only the intent distinctions that change receiver behavior
(tell/ask/propose/confirm/reject) and moves verification from claimed intent
to enforced output — the lesson of 25 years of ACL literature.
