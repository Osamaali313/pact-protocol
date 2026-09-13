#!/usr/bin/env python3
"""Consolidate every §5 live-results table into one paper-ready document.
Zero API calls: reads the result JSONs already on disk. Sources:
  results.json           -> E1 density, E2 per-hop, E4 integrity
  echo_off/on.json       -> L1/L3 (+95% CIs) and echo delta, L2 output tokens
  e8_cpct.json           -> E8 cost-per-correct-task
  e7_caching.json        -> E7 prompt-cache saving
  grammar_probe.json     -> Phase 5 grammar-probe log
Writes results/SECTION5_TABLES.md.

    python3 experiments/section5.py
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
R = os.path.join(ROOT, "results")
SHORT = {"anthropic:claude-haiku-4-5": "haiku-4-5",
         "openrouter:openai/gpt-4o-mini": "gpt-4o-mini",
         "anthropic:claude-sonnet-4-6": "sonnet-4-6"}


def _load(name):
    p = os.path.join(R, name)
    return json.load(open(p)) if os.path.exists(p) else None


def cell(d, metric):
    v = d[metric]
    ci = d.get(metric + "_ci")
    return f"{v:.3f} [{ci[0]:.2f},{ci[1]:.2f}]" if ci else f"{v}"


def main():
    res = _load("results.json")
    off = _load("echo_off.json")["models"]
    on = _load("echo_on.json")["models"]
    e8 = _load("e8_cpct.json")
    e7 = _load("e7_caching.json")
    probe = _load("grammar_probe.json")
    L = ["# PACT §5 — consolidated live results",
         "",
         "All live numbers: temperature=0, repeats=3 (mean [95% bootstrap CI]), "
         "provider usage/billing fields authoritative. Offline density/integrity "
         "from the reproducible E1-E6 suite.", ""]

    # E1 density
    L += ["## E1 - tokens per message (offline, exact o200k_base BPE)", "",
          "| workload | JSON pretty | TOON | PACT | PACT+echo |",
          "|---|---|---|---|---|"]
    for w, row in res["E1_tokens"].items():
        L.append(f"| {w} | {row['JSON pretty']} | {row['TOON']} | {row['PACT']} "
                 f"| {row['PACT+echo']} |")
    L += ["", "*Echo-off PACT is denser than TOON at every size; the ~15-tok "
          "fixed echo makes PACT+echo denser than TOON only above ~a few dozen "
          "rows (3241<3421 at 200; 201>191 at 10).*", ""]

    # L1 + echo delta
    L += ["## L1 - comprehension, echo OFF -> ON (mean [95% CI])", "",
          "| model | JSON | TOON | PACT (no echo) | PACT (echo) | echo delta |",
          "|---|---|---|---|---|---|"]
    for s in off:
        o, n = off[s]["L1_comprehension"], on[s]["L1_comprehension"]
        d = n["PACT"]["accuracy"] - o["PACT"]["accuracy"]
        L.append(f"| {SHORT[s]} | {cell(o['JSON'],'accuracy')} "
                 f"| {cell(o['TOON'],'accuracy')} | {cell(o['PACT'],'accuracy')} "
                 f"| {cell(n['PACT'],'accuracy')} | {d:+.3f} |")
    L += ["", "*Field echo is a real but partial L1 fix (+0.11-0.13 on the "
          "Anthropic models, CIs non-overlapping); it does not reach TOON "
          "parity. Locality is necessary, not sufficient.*", ""]

    # L3 + echo delta
    L += ["## L3 - two-hop end task, echo OFF -> ON (mean [95% CI])", "",
          "| model | JSON | TOON | PACT | PACT+echo | PACT-decoded | "
          "PACT-decoded+echo |", "|---|---|---|---|---|---|---|"]
    for s in off:
        o, n = off[s]["L3_two_hop"], on[s]["L3_two_hop"]
        L.append(f"| {SHORT[s]} | {cell(o['JSON'],'end_task_accuracy')} "
                 f"| {cell(o['TOON'],'end_task_accuracy')} "
                 f"| {cell(o['PACT'],'end_task_accuracy')} "
                 f"| {cell(n['PACT'],'end_task_accuracy')} "
                 f"| {cell(o['PACT-decoded'],'end_task_accuracy')} "
                 f"| {cell(n['PACT-decoded'],'end_task_accuracy')} |")
    L += ["", "*PACT-decoded reaches JSON/TOON-parity end-task accuracy "
          "(sonnet 1.000; gpt-4o-mini 0.639 = TOON). The consumption layer, "
          "not raw reading, is PACT's answer to reading-heavy hops.*", ""]

    # L2 output tokens
    L += ["## L2 - output tokens per message (generation cost; output ~5x "
          "input price)", "", "| model | JSON | TOON | PACT | PACT/JSON |",
          "|---|---|---|---|---|"]
    for s in off:
        g = off[s]["L2_generation"]
        j = g["JSON"]["output_tokens_per_msg"]
        p = g["PACT"]["output_tokens_per_msg"]
        L.append(f"| {SHORT[s]} | {j} | {g['TOON']['output_tokens_per_msg']} "
                 f"| {p} | {p/j:.2f} |")
    L += ["", "*PACT emits ~2.3x fewer output tokens than JSON - a large, "
          "measured generation-cost saving at output pricing.*", ""]

    # E2 per-hop
    e2 = res["E2_consumption"]
    L += ["## E2 - per-hop cost across consumption modes (200-row msg, offline "
          "tokens x list pricing)", "",
          "| mode | sender out tok | receiver in tok | $/hop | "
          f"$ cumulative ({e2['hops']} hops) |", "|---|---|---|---|---|"]
    t = e2["tokens"]
    ins = {"json_model": t["json"], "pact_model_raw": t["pact_echo"],
           "pact_model_keyed": t["keyed"], "pact_code": 0}
    outs = {"json_model": t["json"], "pact_model_raw": t["pact_echo"],
            "pact_model_keyed": t["pact_echo"], "pact_code": t["pact_echo"]}
    for m in ("json_model", "pact_model_raw", "pact_model_keyed", "pact_code"):
        L.append(f"| {m} | {outs[m]} | {ins[m]} | {e2['per_hop_usd'][m]:.6f} "
                 f"| {e2['cumulative_usd'][m][-1]:.4f} |")
    L += ["", "*Re-rendering does not erase the savings: even pact_model_keyed "
          "(a model re-reads a keyed view every hop) is ~half json_model, "
          "because dense output + cached schema dominate.*", ""]

    # E7 caching
    if e7:
        L += ["## E7 - prompt-cache validation on real billing meters", "",
              "| model | pinned prefix | steady-state input saving |",
              "|---|---|---|"]
        for spec, e in e7.items():
            pt = e.get("prefix_tokens")
            L.append(f"| {spec} | {pt if pt else 'n/a'} tokens "
                     f"| {e['steady_state_input_saving']*100:.1f}% |")
        L += ["", "*The cacheable prefix must exceed the provider floor "
              "(Haiku 4.5 = 4096 tok) or cache_control silently no-ops.*", ""]

    # E8 CPCT (headline)
    if e8:
        short = {"claude-haiku-4-5": "haiku-4-5",
                 "openai/gpt-4o-mini": "gpt-4o-mini",
                 "claude-sonnet-4-6": "sonnet-4-6"}
        for exp in ("L1", "L3"):
            L += [f"## E8 - cost-per-correct-task, {exp} (headline metric)", "",
                  "| model | mode | accuracy | cents/correct |",
                  "|---|---|---|---|"]
            for r in e8:
                if r["exp"] != exp:
                    continue
                k = "inf" if r["cpct"] is None else f"{r['cpct']*100:.4f}"
                L.append(f"| {short[r['spec'].split(':',1)[1]]} | {r['mode']} "
                         f"| {r['accuracy']:.3f} | {k} |")
            L.append("")
        L += ["*L1 raw reading: TOON wins CPCT (PACT's weak mode). L3 end task: "
              "PACT-decoded is Pareto-competitive or best - gpt-4o-mini "
              "PACT-decoded+echo is the outright CPCT winner (0.048c), sonnet "
              "PACT-decoded ties JSON accuracy (1.000) below JSON cost. See "
              "figures/fig6_pareto.png.*", ""]

    # grammar probe
    if probe:
        L += ["## Phase 5 - custom-grammar structured-output probe (§8 "
              "limitation)", "",
              "| endpoint | model | api shape | supported | detail |",
              "|---|---|---|---|---|"]
        for o in probe:
            sup = {True: "YES", False: "no", None: "n/a"}[o.get("supported")]
            det = o.get("note") or o.get("sample_output") or \
                f"{o.get('error_type','')}: {o.get('error','')}"
            L.append(f"| {o['endpoint']} | {o['model']} | {o['api']} | {sup} "
                     f"| {str(det)[:90]} |")
        L += ["", "*Custom-grammar structured outputs unsupported via OpenRouter "
              "(both Responses and chat shapes 400); no direct OpenAI key "
              "available to cross-check. True grammar-level L3 enforcement "
              "remains available on self-hosted vLLM/llama.cpp via "
              "gbnf_from_schema.*", ""]

    open(os.path.join(R, "SECTION5_TABLES.md"), "w", encoding="utf-8").write(
        "\n".join(L))
    print("wrote results/SECTION5_TABLES.md (" + str(len(L)) + " lines)")


if __name__ == "__main__":
    main()
