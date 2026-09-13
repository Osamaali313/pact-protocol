"""PACT over the Anthropic SDK — schema in a cache_control'd system block.

pip install anthropic pact-protocol
export ANTHROPIC_API_KEY=...
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import anthropic
from pact import Schema, Field
from pact.client import ask_pact, session_prompt

WALLS = Schema("walls", (
    Field("id", "str"),
    Field("level", "enum", ("L01", "L02", "L03", "L04", "ROOF")),
    Field("fire_rating", "enum", ("EI30", "EI60", "EI90", "EI120", "NR")),
    Field("thickness_mm", "int", lo=50, hi=600),
    Field("load_bearing", "bool"),
    Field("confidence", "float", lo=0.0, hi=1.0),
))

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env

def complete(prompt: str) -> str:
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=[{
            "type": "text",
            "text": session_prompt(WALLS, "You are the extractor agent."),
            # L1 rides Anthropic's prompt cache: ~10x cheaper from call #2.
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")

if __name__ == "__main__":
    msg = ask_pact(complete,
                   ">ask s=planner r=extractor c=7f3a sid=" + WALLS.sid +
                   "\nList 3 plausible fire-rated walls on level L02.",
                   WALLS)
    for rec in msg["records"]:
        print(rec)  # every record schema-valid or PactError was raised
