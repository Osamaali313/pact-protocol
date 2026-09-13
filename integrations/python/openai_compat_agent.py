"""PACT over ANY OpenAI-compatible endpoint: OpenAI, OpenRouter, Groq,
DeepSeek, MiniMax, xAI (Grok) — one file, switch by env vars.

pip install openai pact-protocol
  OpenAI:     export OPENAI_API_KEY=...
  OpenRouter: export PACT_BASE_URL=https://openrouter.ai/api/v1 OPENAI_API_KEY=$OPENROUTER_API_KEY
  Groq:       export PACT_BASE_URL=https://api.groq.com/openai/v1 OPENAI_API_KEY=$GROQ_API_KEY
  DeepSeek:   export PACT_BASE_URL=https://api.deepseek.com OPENAI_API_KEY=$DEEPSEEK_API_KEY
  MiniMax:    export PACT_BASE_URL=https://api.minimax.io/v1 OPENAI_API_KEY=$MINIMAX_API_KEY
  xAI:        export PACT_BASE_URL=https://api.x.ai/v1 OPENAI_API_KEY=$XAI_API_KEY
  Model:      export PACT_MODEL=<provider model id>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openai import OpenAI
from pact import Schema, Field
from pact.client import ask_pact, session_prompt

TASKS = Schema("tasks", (
    Field("task_id", "str"),
    Field("assignee", "enum", ("planner", "extractor", "verifier", "writer")),
    Field("priority", "enum", ("P0", "P1", "P2", "P3")),
    Field("status", "enum", ("open", "running", "done", "failed")),
    Field("est_minutes", "int", lo=0, hi=10000),
))

client = OpenAI(base_url=os.environ.get("PACT_BASE_URL") or None)
MODEL = os.environ.get("PACT_MODEL", "gpt-4.1-mini")
SYSTEM = session_prompt(TASKS, "You are the planner agent.")
# OpenAI-compatible caching is automatic prefix caching: keep SYSTEM
# byte-identical and first in the message list on every call.

def complete(prompt: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""

if __name__ == "__main__":
    msg = ask_pact(complete,
                   f">ask s=user r=planner c=1 sid={TASKS.sid}\n"
                   "Break 'benchmark PACT vs TOON' into 4 tasks.",
                   TASKS)
    for rec in msg["records"]:
        print(rec)
