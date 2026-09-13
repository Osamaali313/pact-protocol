#!/usr/bin/env python3
"""Diagnose live-benchmark failures from results/live_cache.jsonl — free,
no API calls. Answers: were PACT failures mechanical (markdown fences,
trailing prose, true/false-vs-T/F, row-count drift) or genuine?

    python3 experiments/diagnose.py
"""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from pact import Schema, Field, decode, PactError
from pact.client import extract_wire

WALLS = Schema("walls", (
    Field("id", "str"),
    Field("level", "enum", ("L01", "L02", "L03", "L04", "ROOF")),
    Field("fire_rating", "enum", ("EI30", "EI60", "EI90", "EI120", "NR")),
    Field("thickness_mm", "int", lo=50, hi=600),
    Field("load_bearing", "bool"),
    Field("confidence", "float", lo=0.0, hi=1.0),
))
REG = {WALLS.sid: WALLS}
CACHE = os.path.join(ROOT, "results", "live_cache.jsonl")

def old_extract(text):  # what v1 of the harness did
    m = re.search(r">(?:tell|ask|propose|confirm|reject)[\s\S]*", text)
    return m.group(0) if m else text

def classify(text):
    """(old_verdict, new_verdict, reason)"""
    def try_dec(t):
        try:
            decode(t, REG); return True, ""
        except PactError as e:
            return False, str(e)
    old_ok, old_err = try_dec(old_extract(text))
    new_ok, new_err = try_dec(extract_wire(text))
    if old_ok:
        return "valid", "valid", ""
    if new_ok:
        why = "markdown fence / trailing prose (mechanical — fixed extractor recovers it)"
        return "invalid", "valid", why
    e = new_err
    if "bad literal 'true'" in e or "bad literal 'false'" in e or '"true"' in e:
        r = "bool emitted as true/false instead of T/F (prompt-followable)"
    elif "arity" in e:
        r = f"row arity wrong ({e})"
    elif "declared" in e:
        r = f"row-count drift ({e})"
    elif "outside enum" in e:
        r = f"enum violation ({e})"
    elif "unknown schema sid" in e:
        r = "sid wrong/hallucinated"
    elif "malformed header" in e:
        r = "no/failed header line"
    else:
        r = e
    return "invalid", "invalid", r

def main():
    if not os.path.exists(CACHE):
        raise SystemExit("no results/live_cache.jsonl found — run live_bench first")
    rows = [json.loads(l) for l in open(CACHE) if l.strip()]
    pact_gen = [r for r in rows if ">tell" in r.get("text", "")
                or "sid=" in r.get("text", "")]
    print(f"cache entries: {len(rows)}; entries containing PACT attempts: "
          f"{len(pact_gen)}\n")
    stats = {}
    recovered = 0
    for r in pact_gen:
        old_v, new_v, why = classify(r["text"])
        if old_v == "valid":
            continue
        recovered += (new_v == "valid")
        key = (r.get("spec", "?"), "RECOVERED by fixed extractor" if new_v == "valid" else why)
        stats[key] = stats.get(key, 0) + 1
    if not stats:
        print("no PACT decode failures found in cache — all attempts were valid")
        return
    print("PACT failures in cached responses, by model and cause:")
    for (spec, why), n in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {n:3d} × [{spec}] {why}")
    print(f"\nmechanically recoverable with the fixed harness: {recovered}")
    print("Re-run live_bench.py (keep the cache file): re-scoring cached "
          "text is free; only genuinely-new prompts get billed.")

if __name__ == "__main__":
    main()
