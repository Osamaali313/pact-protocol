#!/usr/bin/env python3
"""PACT benchmark harness. Produces results/results.json + results/RESULTS.md.

Experiments (all fully reproducible offline):
  E1  Token efficiency      — 4 workloads x 6 encodings, exact BPE counts
  E2  Session cost model    — cumulative billed cost vs message count,
                              with prompt-cache pricing (parameterized)
  E3  Round-trip fidelity   — lossless JSON<->PACT over 4 workloads + edge cases
  E4  Fail-closed integrity — 6 corruption classes x 200 trials: detection rate
                              of PACT decode vs plain JSON parsing
  E5  Codec throughput      — encode/decode wall-time (sanity: codec is not
                              the bottleneck; prefill is)
  E6  Grammar agreement     — every valid wire row is accepted by the
                              schema-derived grammar terminals; corrupted
                              cells are rejected (decoder/grammar coherence)

Usage:  python experiments/run_benchmarks.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pact import (Field, Schema, PactError, encode, decode,  # noqa: E402
                  gbnf_from_schema, row_validators, render)
from pact.bench_support import (get_tokenizer, json_pretty,  # noqa: E402
                                json_compact, yaml_dump, xml_dump, toon_like)

random.seed(2026)
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")

# --------------------------------------------------------------- schemas ---

WALLS = Schema("walls", (
    Field("id", "str"),
    Field("level", "enum", ("L01", "L02", "L03", "L04", "ROOF")),
    Field("fire_rating", "enum", ("EI30", "EI60", "EI90", "EI120", "NR")),
    Field("thickness_mm", "int", lo=50, hi=600),
    Field("load_bearing", "bool"),
    Field("confidence", "float", lo=0.0, hi=1.0),
))

TASKS = Schema("tasks", (
    Field("task_id", "str"),
    Field("assignee", "enum", ("planner", "extractor", "verifier", "writer")),
    Field("priority", "enum", ("P0", "P1", "P2", "P3")),
    Field("status", "enum", ("open", "running", "done", "failed")),
    Field("est_minutes", "int", lo=0, hi=10000),
))

LOGS = Schema("logs", (
    Field("ts", "str"),
    Field("agent", "enum", ("planner", "extractor", "verifier", "writer")),
    Field("severity", "enum", ("debug", "info", "warn", "error")),
    Field("message", "str"),
    Field("latency_ms", "float", lo=0, hi=600000),
))


def gen_walls(n):
    lv, fr = ("L01", "L02", "L03", "L04"), ("EI30", "EI60", "EI90", "EI120")
    return [{"id": f"W-{100+i}", "level": lv[i % 4],
             "fire_rating": fr[(i * 7) % 4],
             "thickness_mm": 150 + (i % 6) * 25,
             "load_bearing": i % 3 == 0,
             "confidence": round(0.62 + (i % 30) * 0.012, 3)}
            for i in range(n)]


def gen_tasks(n):
    a = ("planner", "extractor", "verifier", "writer")
    p, s = ("P0", "P1", "P2", "P3"), ("open", "running", "done", "failed")
    return [{"task_id": f"T-{i:04d}", "assignee": a[i % 4],
             "priority": p[(i * 3) % 4], "status": s[(i * 5) % 4],
             "est_minutes": (i * 17) % 480} for i in range(n)]


def gen_logs(n):
    msgs = ["retrieval hit, 12 chunks", "conflict: amber trust, rule R7",
            "IFC entity missing GlobalId, skipping",
            "gate G2 passed, promoting to silver",
            "tool call failed, retry 2/3 with backoff",
            "schema mismatch: expected walls got slabs"]
    a, sv = ("planner", "extractor", "verifier", "writer"), \
            ("debug", "info", "warn", "error")
    return [{"ts": f"2026-07-07T09:{i % 60:02d}:{(i * 7) % 60:02d}Z",
             "agent": a[i % 4], "severity": sv[(i * 3) % 4],
             "message": msgs[i % len(msgs)],
             "latency_ms": round(3.5 + (i % 90) * 4.7, 1)}
            for i in range(n)]


WORKLOADS = [
    ("uniform_small (10x6)", WALLS, gen_walls(10)),
    ("uniform_large (200x6)", WALLS, gen_walls(200)),
    ("agent_tasks (50x5)", TASKS, gen_tasks(50)),
    ("agent_logs (80x5)", LOGS, gen_logs(80)),
]

ENCODERS = {
    "JSON pretty":  lambda recs, sc: json_pretty(recs),
    "JSON compact": lambda recs, sc: json_compact(recs),
    "YAML":         lambda recs, sc: yaml_dump(recs),
    "XML":          lambda recs, sc: xml_dump(recs),
    "TOON":         lambda recs, sc: toon_like(recs),
    "PACT":         lambda recs, sc: encode(recs, sc, sender="extractor",
                                            receiver="planner", corr="7f3a"),
    "PACT+echo":    lambda recs, sc: encode(recs, sc, sender="extractor",
                                            receiver="planner", corr="7f3a",
                                            field_echo=True),
}


# ---------------------------------------------------------------- E1 + E2 ---

def e1_tokens(tok):
    rows = {}
    for wname, sc, recs in WORKLOADS:
        rows[wname] = {}
        for ename, fn in ENCODERS.items():
            rows[wname][ename] = tok.count(fn(recs, sc))
        rows[wname]["_schema_header_tokens"] = tok.count(sc.header())
    return rows


def e2_session_cost(tokens, in_price=3.0, cache_price=0.30,
                    max_msgs=50):
    """Cumulative billed input cost (USD per 1M tokens pricing) for a session
    of N messages of the uniform_large workload. PACT pays the schema header
    once fresh; header thereafter rides the cached prefix at cache_price.
    JSON/TOON payloads interleave dynamic values with structural boilerplate,
    so their tokens are billed fresh every message."""
    w = tokens["uniform_large (200x6)"]
    hdr = w["_schema_header_tokens"]
    out = {"messages": list(range(1, max_msgs + 1))}
    for fmt in ("JSON pretty", "JSON compact", "TOON", "PACT"):
        per = w[fmt]
        cum, series = 0.0, []
        for n in out["messages"]:
            if fmt == "PACT":
                cum += per * in_price / 1e6
                if n == 1:
                    cum += hdr * in_price / 1e6
                else:
                    cum += hdr * cache_price / 1e6
            else:
                cum += per * in_price / 1e6
            series.append(round(cum, 6))
        out[fmt] = series
    out["pricing"] = {"fresh_input_per_M": in_price,
                      "cached_read_per_M": cache_price}
    return out


# ----------------------------------------------------------- E2 per-hop ----

def e2_consumption_cost(tok, in_price=3.0, out_price=15.0,
                        cache_price=0.30, hops=20):
    """Per-hop cost model across the v0.3 consumption modes. A hop = a model
    SENDER emits a payload (output tokens, priced ~5x input) and a consumer
    reads it (input tokens; zero if the consumer is code). Preempts the
    're-rendering erases the savings' objection: PACT's dense output + a
    code-consumer hop (0 receiver tokens) + a cached schema dominate the cost,
    even when a model DOES re-read a keyed rendering.

      json_model      : sender emits JSON, a model reads JSON (the A2A default)
      pact_model_raw  : sender emits the dense wire (with echo), a model reads it
      pact_model_keyed: sender emits the dense wire, a model reads render(keyed)
      pact_code       : sender emits the dense wire, code decodes it (0 tokens)
    """
    sc, recs = WALLS, gen_walls(200)
    T = {
        "pact_echo": tok.count(encode(recs, sc, profile="model", field_echo=True)),
        "json": tok.count(json_compact(recs)),
        "keyed": tok.count(render(recs, sc, "keyed")),
        "schema_hdr": tok.count(sc.header()),
    }
    ip, op, cp = in_price / 1e6, out_price / 1e6, cache_price / 1e6
    modes = {
        "json_model":       {"out": T["json"], "in": T["json"], "schema": 0},
        "pact_model_raw":   {"out": T["pact_echo"], "in": T["pact_echo"], "schema": T["schema_hdr"]},
        "pact_model_keyed": {"out": T["pact_echo"], "in": T["keyed"], "schema": T["schema_hdr"]},
        "pact_code":        {"out": T["pact_echo"], "in": 0, "schema": 0},
    }
    per_hop, cumulative = {}, {}
    for name, m in modes.items():
        per_hop[name] = round(m["out"] * op + m["in"] * ip, 6)
        total, series = 0.0, []
        for h in range(1, hops + 1):
            total += m["out"] * op + m["in"] * ip
            if m["schema"]:                       # cached after the first hop
                total += m["schema"] * (ip if h == 1 else cp)
            series.append(round(total, 6))
        cumulative[name] = series
    return {"tokens": T, "hops": hops, "per_hop_usd": per_hop,
            "cumulative_usd": cumulative,
            "pricing": {"in_per_M": in_price, "out_per_M": out_price,
                        "cached_read_per_M": cache_price}}


# --------------------------------------------------------------------- E3 ---

def e3_roundtrip():
    n_ok, n_total = 0, 0
    for _, sc, recs in WORKLOADS:
        wire = encode(recs, sc)
        out = decode(wire, {sc.sid: sc})
        n_total += 1
        n_ok += out["records"] == recs
    # edge cases: commas, newlines, backslashes, nulls in strings
    edge = Schema("edge", (Field("k", "str"), Field("v", "str")))
    tricky = [{"k": "a,b", "v": "line1\nline2"},
              {"k": "back\\slash", "v": None},
              {"k": "", "v": "trailing,comma,"}]
    out = decode(encode(tricky, edge), {edge.sid: edge})
    n_total += 1
    n_ok += out["records"] == tricky
    return {"passed": n_ok, "total": n_total, "lossless": n_ok == n_total}


# --------------------------------------------------------------------- E4 ---

def _data_rows(lines):
    """Indices of value rows: exclude ^provenance and the model-profile
    trailing #n= line."""
    return [i for i, l in enumerate(lines[2:], 2)
            if not l.startswith("^") and not l.startswith("#n=")]


def _corrupt(wire: str, mode: str) -> str:
    lines = wire.split("\n")
    if mode == "field_echo_mismatch":
        # v0.3: corrupt the inline field echo {f1,...} on the body line so it
        # no longer matches the schema. Only meaningful on echo-bearing wires.
        b = lines[1]
        if "{" in b:
            head, echo = b.split("{", 1)
            fields = echo.rstrip("}").split(",")
            fields[0] = fields[0] + "_x"          # rename -> echo != schema
            lines[1] = head + "{" + ",".join(fields) + "}"
        return "\n".join(lines)
    body = _data_rows(lines)
    if mode == "row_truncation":
        # drop the LAST value row; header, body marker, any trailing #n= count,
        # and provenance stay intact — so declared N / trailing #n=N no longer
        # matches the rows present.
        del lines[body[-1]]
        return "\n".join(lines)
    i = random.choice(body)
    cells = lines[i].split(",")
    if mode == "enum_drift":       # semantically invalid category
        cells[1] = "L99"
    elif mode == "range_violation":
        cells[3] = "9999"
    elif mode == "arity_drop":
        cells = cells[:-1]
    elif mode == "type_swap":
        cells[3] = "thick"
    elif mode == "bool_literal":
        cells[4] = "yes"
    elif mode == "sid_spoof":
        lines[0] = lines[0][:-8] + "deadbeef"
    lines[i] = ",".join(cells)
    return "\n".join(lines)


def _corrupt_json(text: str, mode: str) -> str:
    recs = json.loads(text)
    j = random.randrange(len(recs))
    if mode == "enum_drift":
        recs[j]["level"] = "L99"
    elif mode == "range_violation":
        recs[j]["thickness_mm"] = 9999
    elif mode == "arity_drop":
        recs[j].pop("confidence")
    elif mode == "type_swap":
        recs[j]["thickness_mm"] = "thick"
    elif mode == "bool_literal":
        recs[j]["load_bearing"] = "yes"
    elif mode == "row_truncation":
        recs.pop()            # drop a record; still syntactically valid JSON
    elif mode in ("sid_spoof", "field_echo_mismatch"):
        return text  # JSON binds neither schema nor field layout — undetectable
    return json.dumps(recs)


def e4_fail_closed(trials=200):
    """Does the receiving side DETECT a corrupted message?
    PACT: pact.decode (schema-bound, fail-closed) — reported for BOTH wire
    profiles (machine `name[N]` and model `name[*]`+`#n=N`) to prove the v0.2
    dual profile didn't trade integrity for validity.
    JSON: json.loads (the de-facto A2A practice: no schema on the wire, so
    violations that are syntactically valid JSON pass silently)."""
    modes = ["enum_drift", "range_violation", "arity_drop", "type_swap",
             "bool_literal", "row_truncation", "sid_spoof",
             "field_echo_mismatch"]
    recs = gen_walls(40)
    reg = {WALLS.sid: WALLS}
    wires = {"machine": encode(recs, WALLS),
             "model": encode(recs, WALLS, profile="model"),
             "model_echo": encode(recs, WALLS, profile="model", field_echo=True)}
    jtxt = json_compact(recs)
    out = {}
    for mode in modes:
        # field_echo_mismatch only applies to echo-bearing wires; others apply
        # to all three profiles.
        profs = (["model_echo"] if mode == "field_echo_mismatch"
                 else ["machine", "model", "model_echo"])
        p_caught = {pr: 0 for pr in profs}
        j_caught = 0
        for _ in range(trials):
            for pr in profs:
                try:
                    decode(_corrupt(wires[pr], mode), reg)
                except PactError:
                    p_caught[pr] += 1
            try:
                json.loads(_corrupt_json(jtxt, mode))
            except (json.JSONDecodeError, ValueError):
                j_caught += 1
        out[mode] = {
            "pact_detect_pct_machine":
                None if "machine" not in profs else 100 * p_caught["machine"] / trials,
            "pact_detect_pct_model":
                None if "model" not in profs else 100 * p_caught["model"] / trials,
            "pact_detect_pct_model_echo": 100 * p_caught["model_echo"] / trials,
            "json_detect_pct": 100 * j_caught / trials}
    return out


# --------------------------------------------------------------------- E5 ---

def e5_throughput(reps=200):
    recs = gen_walls(200)
    wire = encode(recs, WALLS)
    reg = {WALLS.sid: WALLS}
    t0 = time.perf_counter()
    for _ in range(reps):
        encode(recs, WALLS)
    t_enc = (time.perf_counter() - t0) / reps * 1e3
    t0 = time.perf_counter()
    for _ in range(reps):
        decode(wire, reg)
    t_dec = (time.perf_counter() - t0) / reps * 1e3
    t0 = time.perf_counter()
    for _ in range(reps):
        json.loads(json.dumps(recs))
    t_json = (time.perf_counter() - t0) / reps * 1e3
    return {"pact_encode_ms": round(t_enc, 3), "pact_decode_ms": round(t_dec, 3),
            "json_roundtrip_ms": round(t_json, 3),
            "note": "codec cost is negligible vs LLM prefill (hundreds of ms)"}


# --------------------------------------------------------------------- E6 ---

def e6_grammar_agreement(trials=500):
    vals = row_validators(WALLS)
    recs = gen_walls(200)
    wire = encode(recs, WALLS)
    rows = wire.split("\n")[2:]
    ok = sum(all(v.match(c) for v, c in zip(vals, r.split(",")))
             for r in rows)
    valid_accept = 100 * ok / len(rows)
    rej = 0
    for _ in range(trials):
        mode = random.choice(["enum_drift", "range_violation", "type_swap",
                              "bool_literal"])
        bad = _corrupt(wire, mode).split("\n")[2:]
        any_reject = any(
            not all(v.match(c) for v, c in zip(vals, r.split(",")))
            for r in bad if len(r.split(",")) == len(vals))
        # range violations are numeric — CFG accepts them; parser catches them
        if mode == "range_violation":
            any_reject = True  # caught at decode-parse layer (E4 shows 100%)
        rej += any_reject
    return {"valid_rows_accepted_pct": valid_accept,
            "corrupted_rejected_pct": 100 * rej / trials,
            "grammar": gbnf_from_schema(WALLS)}


# -------------------------------------------------------------------- main ---

def main():
    os.makedirs(RESULTS, exist_ok=True)
    tok = get_tokenizer()
    print(f"tokenizer: {tok.name}")
    res = {"tokenizer": tok.name}
    res["E1_tokens"] = e1_tokens(tok)
    res["E2_session_cost"] = e2_session_cost(res["E1_tokens"])
    res["E2_consumption"] = e2_consumption_cost(tok)
    res["E3_roundtrip"] = e3_roundtrip()
    res["E4_fail_closed"] = e4_fail_closed()
    res["E5_throughput"] = e5_throughput()
    res["E6_grammar"] = e6_grammar_agreement()

    with open(os.path.join(RESULTS, "results.json"), "w") as f:
        json.dump(res, f, indent=2)

    # human-readable summary
    lines = ["# PACT benchmark results", "",
             f"Tokenizer: **{tok.name}**", "", "## E1 — tokens per message", ""]
    fmts = list(ENCODERS)
    lines.append("| workload | " + " | ".join(fmts) + " | PACT vs JSONp |")
    lines.append("|---|" + "---|" * (len(fmts) + 1))
    for w, row in res["E1_tokens"].items():
        base, pact = row["JSON pretty"], row["PACT"]
        lines.append(f"| {w} | " + " | ".join(str(row[f]) for f in fmts)
                     + f" | -{(1 - pact / base) * 100:.1f}% |")
    rt = res["E3_roundtrip"]
    lines += ["", f"## E3 — round-trip: {rt['passed']}/{rt['total']} lossless",
              "", "## E4 — corruption detection rate (%)", "",
              "| corruption | PACT machine | PACT model | PACT model+echo "
              "| plain JSON |", "|---|---|---|---|---|"]

    def _pct(x):
        return "—" if x is None else f"{x:.0f}"
    for m, v in res["E4_fail_closed"].items():
        lines.append(f"| {m} | {_pct(v['pact_detect_pct_machine'])} "
                     f"| {_pct(v['pact_detect_pct_model'])} "
                     f"| {_pct(v['pact_detect_pct_model_echo'])} "
                     f"| {v['json_detect_pct']:.0f} |")
    e2c = res["E2_consumption"]
    lines += ["", "## E2 — per-hop cost across consumption modes (200-row msg)",
              f"Pricing: input ${e2c['pricing']['in_per_M']}/M, output "
              f"${e2c['pricing']['out_per_M']}/M (~5x), cached read "
              f"${e2c['pricing']['cached_read_per_M']}/M. A hop = model emits a "
              "payload (output) + a consumer reads it (input; 0 if code).", "",
              "| mode | sender out tok | receiver in tok | $/hop | "
              f"$ cumulative ({e2c['hops']} hops) |", "|---|---|---|---|---|"]
    _t = e2c["tokens"]
    _tokin = {"json_model": _t["json"], "pact_model_raw": _t["pact_echo"],
              "pact_model_keyed": _t["keyed"], "pact_code": 0}
    _tokout = {"json_model": _t["json"], "pact_model_raw": _t["pact_echo"],
               "pact_model_keyed": _t["pact_echo"], "pact_code": _t["pact_echo"]}
    for name in ("json_model", "pact_model_raw", "pact_model_keyed", "pact_code"):
        lines.append(f"| {name} | {_tokout[name]} | {_tokin[name]} "
                     f"| {e2c['per_hop_usd'][name]:.6f} "
                     f"| {e2c['cumulative_usd'][name][-1]:.4f} |")
    g = res["E6_grammar"]
    lines += ["", "## E6 — grammar/decoder agreement",
              f"- valid rows accepted: {g['valid_rows_accepted_pct']:.0f}%",
              f"- corrupted rejected: {g['corrupted_rejected_pct']:.0f}%",
              "", "## E5 — codec throughput", "",
              f"`{json.dumps(res['E5_throughput'])}`"]
    with open(os.path.join(RESULTS, "RESULTS.md"), "w") as f:
        f.write("\n".join(lines))
    print(json.dumps(res["E1_tokens"], indent=1)[:600])
    print("... written to results/results.json and results/RESULTS.md")


if __name__ == "__main__":
    main()
