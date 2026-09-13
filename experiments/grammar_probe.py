#!/usr/bin/env python3
"""Phase 5 — probe whether custom context-free grammar structured outputs
(Lark, OpenAI Responses API) are reachable for L3-style enforcement over a
plain API. Honest probe: try the real grammar request shape, log the exact
outcome (success OR the precise error) — never substitute json_schema mode.

Order: OpenRouter (OPENROUTER_API_KEY) first; if it declines, and a direct
OPENAI_API_KEY is present, try OpenAI directly once (OpenRouter's "no" does not
prove OpenAI's "no"). Writes results/GRAMMAR_PROBE.md.

    python3 experiments/grammar_probe.py
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
RESULTS = os.path.join(ROOT, "results")
PROBE_DATE = os.environ.get("PROBE_DATE", "2026-08-04")  # today, per harness ctx


def load_dotenv():
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# A minimal Lark grammar: the probe tests SUPPORT, not the full schema. If a
# provider accepts this, gbnf_from_schema output would be translated to Lark.
LARK = 'start: "T" | "F"'
PROMPT = "Reply with exactly one character: T or F. Is 3 greater than 2?"


def try_responses_grammar(client, model, endpoint):
    """OpenAI Responses API custom-grammar shape."""
    r = client.responses.create(
        model=model,
        input=PROMPT,
        text={"format": {"type": "grammar", "syntax": "lark", "definition": LARK}},
    )
    out = getattr(r, "output_text", None) or str(r)[:200]
    return {"endpoint": endpoint, "model": model, "api": "responses.grammar",
            "supported": True, "sample_output": out}


def try_chat_grammar(client, model, endpoint):
    """Some OpenAI-compatible stacks accept a grammar response_format on
    chat.completions; probe that shape too."""
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT}],
        response_format={"type": "grammar", "grammar": {"syntax": "lark",
                                                        "definition": LARK}},
        max_tokens=4,
    )
    return {"endpoint": endpoint, "model": model, "api": "chat.grammar",
            "supported": True, "sample_output": r.choices[0].message.content}


def probe(base_url, key_env, model, label):
    key = os.environ.get(key_env)
    if not key:
        return [{"endpoint": label, "model": model, "api": "-",
                 "supported": None, "note": f"no {key_env} in .env — skipped"}]
    from openai import OpenAI
    client = OpenAI(base_url=base_url, api_key=key)
    results = []
    for fn in (try_responses_grammar, try_chat_grammar):
        try:
            results.append(fn(client, model, label))
        except Exception as e:
            results.append({"endpoint": label, "model": model,
                            "api": fn.__name__.replace("try_", ""),
                            "supported": False,
                            "error_type": type(e).__name__,
                            "error": str(e)[:300]})
    return results


def main():
    load_dotenv()
    os.makedirs(RESULTS, exist_ok=True)
    outcomes = []
    outcomes += probe("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                      "openai/gpt-4o-mini", "OpenRouter")
    # Only escalate to direct OpenAI if OpenRouter did not clearly support it.
    supported = any(o.get("supported") for o in outcomes)
    if not supported:
        outcomes += probe(None, "OPENAI_API_KEY", "gpt-4o-mini", "OpenAI-direct")

    supported = any(o.get("supported") for o in outcomes)
    lines = ["# Phase 5 — custom-grammar structured-output probe", "",
             f"Probed {PROBE_DATE}. Tests whether Lark/CFG-constrained decoding "
             "(true L3 enforcement) is reachable over a plain API. No "
             "json_schema substitution — grammar or nothing.", "",
             "| endpoint | model | api shape | supported | detail |",
             "|---|---|---|---|---|"]
    for o in outcomes:
        sup = {True: "**YES**", False: "no", None: "n/a"}[o.get("supported")]
        detail = o.get("note") or o.get("sample_output") or \
            f"{o.get('error_type', '')}: {o.get('error', '')}"
        lines.append(f"| {o['endpoint']} | {o['model']} | {o['api']} | {sup} "
                     f"| {str(detail)[:120]} |")
    verdict = ("SUPPORTED — translate gbnf_from_schema to Lark and add an "
               "--enforce grammar L2 arm." if supported else
               "UNSUPPORTED via the probed endpoints — logged as a measured "
               "limitation (§8). True grammar-level L3 enforcement remains "
               "available on self-hosted vLLM/llama.cpp via gbnf_from_schema.")
    lines += ["", f"**Verdict:** {verdict}", ""]
    open(os.path.join(RESULTS, "GRAMMAR_PROBE.md"), "w",
         encoding="utf-8").write("\n".join(lines))
    json.dump(outcomes, open(os.path.join(RESULTS, "grammar_probe.json"), "w"),
              indent=1)
    print("\n".join(lines))
    print("\nwritten: results/GRAMMAR_PROBE.md + results/grammar_probe.json")


if __name__ == "__main__":
    main()
