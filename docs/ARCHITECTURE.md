# PACT Architecture

## Layer model

```mermaid
flowchart TB
    subgraph L0["L0 — Transport (existing, unchanged)"]
        T1["MCP / A2A / ACP / HTTP / message bus"]
    end
    subgraph L1["L1 — Schema Negotiation (once per session)"]
        S1["Typed schema: fields, enums, ranges"]
        S2["sid = sha256(canonical schema)[..8]"]
        S3["Pinned into prompt-cached prefix of BOTH agents"]
    end
    subgraph L2["L2 — Wire Encoding (every message)"]
        W1["Header: performative, s, r, corr, sid"]
        W2["Tabular value rows — no keys, quotes, braces"]
        W3["Provenance: ^key=source"]
    end
    subgraph L3["L3 — Constrained Generation Contract"]
        G1["GBNF grammar derived mechanically from schema"]
        G2["Compiled into responder decoder (Outlines / llama.cpp / vLLM)"]
        G3["Reply valid-by-construction; parse is fail-closed"]
    end
    H["Human / Documents"] -->|"H2A bridge (Logic-LM pattern)"| L1
    L1 --> L2 --> L3 --> APP["Consuming agent logic"]
    L0 -.->|carries| L2
```

## Message lifecycle

```mermaid
sequenceDiagram
    participant H as Human/Doc
    participant B as Bridge
    participant A as Agent A
    participant X as Agent B
    A->>X: !schema walls sid=226909cf (once)
    X-->>A: confirm sid
    Note over A,X: sid rides the cached prefix — ~10x cheaper thereafter
    H->>B: "fire-rated walls on level 2?"
    B->>A: >ask ... sid=226909cf
    A->>X: >ask + grammar contract
    X->>X: constrained decode (valid by construction)
    X-->>A: >tell walls[12] rows + ^provenance
    A->>A: fail-closed parse (enum/range/arity/sid)
    A-->>B: validated records
    B-->>H: NL answer + provenance citations
```

## Decision flow at the receiver

```mermaid
flowchart LR
    IN[wire message] --> HDR{header ok?}
    HDR -- no --> REJ[PactError: reject + renegotiate]
    HDR -- yes --> SID{sid known?}
    SID -- no --> REJ
    SID -- yes --> ROWS{arity, literals,\nenums, ranges ok?}
    ROWS -- no --> REJ
    ROWS -- yes --> CNT{row count = declared?}
    CNT -- no --> REJ
    CNT -- yes --> OK[typed records + provenance]
```

## Wire grammar (EBNF, normative)

```
message      = header NL bodyline { NL row } { NL prov } [ NL trailer ] ;
header       = ">" performative " s=" ident " r=" ident " c=" ident " sid=" sid ;
performative = "tell" | "ask" | "propose" | "confirm" | "reject" ;
bodyline     = name marker [ echo ] ;
marker       = "[" number "]"                     (* machine profile: enforced *)
             | "[*]" ;                            (* model profile: see trailer *)
echo         = "{" name { "," name } "}" ;        (* v0.3 optional field echo *)
trailer      = "#n=" number ;                     (* model-profile row count *)
row          = cell { "," cell } ;                (* arity = |schema.fields| *)
cell         = "~" | typed-literal ;              (* per-field, from schema *)
prov         = "^" key "=" source ;
sid          = 8 * hexdigit ;                     (* sha256(canonical)[..8] *)
```

Escaping in `str` cells: `\,` `\n` `\\`. Booleans: `T`/`F`. Null: `~`.

**Profiles (v0.2).** *machine* (`name[N]`, codec→codec) enforces the leading
count. *model* (`name[*]`, LLM-emitted) carries the count in the trailing
`#n=` line — enforced when present; absent ⇒ `count_verified=false`. **Field
echo (v0.3)** is an optional `{f1,…}` rendering of the negotiated schema on the
body line; it is verified against the schema fail-closed and does **not** change
canonicalization or the `sid` (the sid hashes the type contract, not the
message syntax — `PROTOCOL_VERSION` is 0.3 while the sid-input `Schema.version`
stays 0.2). See RFC-02 (trailing count) and RFC-03 (field echo).

## Invariants

1. **Fail-closed**: any violation raises; there is no repair path.
2. **Grammar/decoder coherence**: `gbnf_from_schema` and `decode` accept
   exactly the same language (verified by E6).
3. **Content addressing**: identical schemas → identical `sid` across
   implementations (canonicalization is normative).
4. **Cache split**: everything static (schema, constraints) lives in the
   prefix; everything dynamic (values) lives in the message.

## Consumption layer (v0.3)

The wire is **codec-to-codec**: dense, positional, schema-bound. How a
*receiver* consumes a message is a separate concern, exposed in v0.3 as
`render(records, schema, style)`. The wire never changes; only the view a model
reads does.

| Consumer | How it reads the message | Model tokens |
|---|---|---|
| **code** | `decode()` → typed dict records, used directly | **0** |
| **model (rendered)** | `decode()` then `render(style=keyed / table)` — labels next to values | render size |
| **model-raw** | reads the dense wire directly; needs the field echo `name[*]{fields}` for locality | wire size |

A code hop costs zero receiver tokens; a model hop pays only for the rendered
view or the raw wire. This is why re-rendering does **not** erase PACT's
savings: the E2 per-hop cost model (results/RESULTS.md) shows dense-output
generation + code-consumer hops + the cached schema dominate total cost, even
when a model re-reads a keyed rendering every hop.

## Known design debts

- Uniform-record bias: nested/heterogeneous payloads need a dialect
  (MLIR-style progressive lowering) — RFC-01.
- CFGs cannot express numeric ranges; ranges enforce at parse (still
  fail-closed), grammar enforces type shape only.
- API-only models without decoder hooks degrade L3 to validate-and-retry.
