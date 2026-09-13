# PACT × provider SDKs — integration guide

PACT is transport- and vendor-neutral by construction: the codec needs no
network, and the enforcement loop needs only a `complete(prompt) -> text`
function. Wrap any SDK call in that and you have PACT agents on that provider.

## Compatibility matrix

| Provider / SDK | Chat API style | Prompt caching | L3 enforcement mode | Example |
|---|---|---|---|---|
| **Anthropic** (`anthropic` py / `@anthropic-ai/sdk` ts) | Messages API | explicit `cache_control` breakpoints → put `session_prompt(schema)` in the FIRST system block and mark it | validate-and-retry (`ask_pact`) | `python/anthropic_agent.py`, `typescript/anthropic.ts` |
| **OpenAI** (`openai` py/ts) | Chat Completions / Responses | automatic prefix caching (≥1024-token prefixes) → keep schema at prompt start, byte-identical across calls | validate-and-retry | `python/openai_compat_agent.py`, `typescript/openai.ts` |
| **OpenRouter** | OpenAI-compatible | pass-through to underlying provider | validate-and-retry | same file — set `base_url="https://openrouter.ai/api/v1"` |
| **Groq** | OpenAI-compatible | prefix caching on supported models | validate-and-retry | same file — `base_url="https://api.groq.com/openai/v1"` |
| **DeepSeek** | OpenAI-compatible | context caching (automatic, discounted cache hits) | validate-and-retry | same file — `base_url="https://api.deepseek.com"` |
| **MiniMax** | OpenAI-compatible | provider-side | validate-and-retry | same file — `base_url="https://api.minimax.io/v1"` (check region) |
| **xAI (Grok)** | OpenAI-compatible | prefix caching | validate-and-retry | same file — `base_url="https://api.x.ai/v1"` |
| **Vercel AI SDK** (`ai` npm) | unified `generateText` over any provider | inherits provider's caching | validate-and-retry | `typescript/vercel-ai.ts` |
| **vLLM / llama.cpp / Outlines** (self-hosted) | OpenAI-compatible or native | RadixAttention / prefix cache | **true L3: pass `gbnf_from_schema(schema)` to the decoder → valid-by-construction, zero retries** | `python/vllm_grammar.py` |

One integration pattern covers seven of the providers above, because they all
speak the OpenAI Chat Completions dialect — only `base_url` and the API key
change. That is deliberate: adopting PACT means changing the *payload*, not
the stack.

## The pattern (identical in every file)

1. **Cache-align the static part.** `session_prompt(schema)` — protocol
   instructions + the negotiated schema header — goes at the very start of the
   system prompt, byte-identical on every call. Anthropic: mark it with
   `cache_control: {type: "ephemeral"}`. OpenAI-compatible: automatic once the
   prefix repeats. This is L1 riding the provider's cache.
2. **Send values only.** The user/agent turn carries the PACT `ask`/`tell`
   message — no keys, no JSON scaffolding. This is L2.
3. **Enforce the reply.** API models: `ask_pact(...)` / `askPact(...)` decodes
   fail-closed and retries with the exact violation as feedback (bounded,
   then raises — the caller never sees an unvalidated record). Self-hosted
   models: install the schema-derived GBNF grammar in the decoder and the
   loop degenerates to a single always-valid call. This is L3.

## Keys and subscriptions

Everything runs on the user's own credentials — standard env vars
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`,
`DEEPSEEK_API_KEY`, `MINIMAX_API_KEY`, `XAI_API_KEY`). PACT adds no service,
no proxy, no account: it is a payload discipline plus a validator, so whatever
plan or subscription a person already has keeps working, just with 49–71%
fewer payload tokens and fail-closed replies.

## Honest status

These integration files are reference implementations: the PACT layers in
them (codec, session prompt, retry loop, grammar synthesis) are covered by
the offline test suites in this repo (Python: benchmark suite; TypeScript:
8/8 tests incl. a mocked provider exercising the retry loop). The live SDK
calls themselves require your API keys and are smoke-test scripts, not part
of the offline-verified benchmark set. Endpoints/base URLs current as of
July 2026 — verify against each provider's docs before production use.
