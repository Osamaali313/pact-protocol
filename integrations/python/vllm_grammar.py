"""True L3 on self-hosted models: schema-derived grammar in the decoder.
Replies are valid-by-construction — ask_pact's retry loop never fires.

Server:  vllm serve <model>       (or llama.cpp with --grammar-file)
Client:  pip install openai pact-protocol
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openai import OpenAI
from pact import Schema, Field, decode, gbnf_from_schema
from pact.client import session_prompt

WALLS = Schema("walls", (
    Field("id", "str"),
    Field("level", "enum", ("L01", "L02", "L03", "L04", "ROOF")),
    Field("fire_rating", "enum", ("EI30", "EI60", "EI90", "EI120", "NR")),
    Field("thickness_mm", "int", lo=50, hi=600),
    Field("load_bearing", "bool"),
    Field("confidence", "float", lo=0.0, hi=1.0),
))

client = OpenAI(base_url=os.environ.get("PACT_BASE_URL",
                                        "http://localhost:8000/v1"),
                api_key=os.environ.get("OPENAI_API_KEY", "EMPTY"))

resp = client.chat.completions.create(
    model=os.environ.get("PACT_MODEL", "meta-llama/Llama-3.1-8B-Instruct"),
    messages=[{"role": "system", "content": session_prompt(WALLS)},
              {"role": "user", "content":
               f">ask s=planner r=extractor c=9 sid={WALLS.sid}\n"
               "Emit 3 example walls on L01."}],
    # vLLM structured-output extension: grammar-constrained decoding (L3).
    extra_body={"guided_grammar": gbnf_from_schema(WALLS),
                "guided_decoding_backend": "outlines"},
)
wire = resp.choices[0].message.content or ""
msg = decode(wire, {WALLS.sid: WALLS})   # still fail-closed (ranges, count)
print(msg["records"])
