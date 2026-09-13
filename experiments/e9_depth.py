#!/usr/bin/env python3
"""E9 — depth divergence: does the fail-closed guarantee compound over a deep
pipeline? A k-hop pipeline of TRANSFORMING hops (each narrows further, no
relay); a structural corruption (thickness_mm := 9999, out of range) is injected
on the wire between hops with probability p. A JSON receiver `json.loads` accepts
it silently (still valid JSON) and it propagates into the final count; a PACT
receiver `decode` catches it fail-closed and retries the hop clean.

Metric per depth k: final-task accuracy (JSON vs PACT) + per-hop attribution
(JSON silent corruptions vs PACT detected+retried corruptions).

Pre-registered null: if JSON accuracy does NOT degrade as k grows, that is
reported as-is (no divergence claim).

    python3 experiments/e9_depth.py --dry-run
    python3 experiments/e9_depth.py
"""
import os
import re
import sys
import json
import random

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
import live_bench as lb  # noqa: E402  (Provider, encode, decode, pact_system, ...)

RESULTS = os.path.join(ROOT, "results")
FIG = os.path.join(RESULTS, "figures")
KS = (2, 4, 6, 8)

# Transforming hops — each NARROWS further (no idempotent relay). Filter h runs
# at hop h; the final hop aggregates (count). An early silent corruption that
# inflates a value survives later thickness filters -> wrong final count.
FILTERS = [
    ("keep only records whose level is L01, L02, or L03",
     lambda r: r["level"] in ("L01", "L02", "L03")),
    ("keep only records whose thickness_mm is greater than 150",
     lambda r: r["thickness_mm"] > 150),
    ("keep only records whose fire_rating is not NR",
     lambda r: r["fire_rating"] != "NR"),
    ("keep only records whose thickness_mm is greater than 250",
     lambda r: r["thickness_mm"] > 250),
    ("keep only records whose confidence is greater than 0.70",
     lambda r: r["confidence"] > 0.70),
    ("keep only records whose load_bearing is true",
     lambda r: r["load_bearing"] is True),
    ("keep only records whose thickness_mm is greater than 350",
     lambda r: r["thickness_mm"] > 350),
]


def base(i):
    rng = random.Random(9000 + i)
    lv = ("L01", "L02", "L03", "L04", "ROOF")
    fr = ("EI30", "EI60", "EI90", "EI120", "NR")
    return [{"id": f"W{i}-{j}", "level": rng.choice(lv),
             "fire_rating": rng.choice(fr),
             "thickness_mm": rng.choice([80, 120, 180, 220, 280, 340, 420, 520]),
             "load_bearing": rng.random() < 0.5,
             "confidence": round(rng.uniform(0.5, 0.99), 3)}
            for j in range(16)]


def truth(R0, k):
    rows = list(R0)
    for _, fn in FILTERS[:k - 1]:
        rows = [r for r in rows if fn(r)]
    return len(rows)


def emit(prov, arm, records, instruction):
    if arm == "PACT":
        sys_p = lb.pact_system("You transform PACT record sets.")
        body = lb.encode(records, lb.WALLS, sender="a", receiver="b", corr="e9")
        return prov.complete(sys_p, f"{body}\n\n{instruction}\nEmit ONLY the "
                                    "resulting PACT message.")
    sys_p = "Data arrives as a JSON array of records."
    body = json.dumps(records, separators=(",", ":"))
    return prov.complete(sys_p, f"{body}\n\n{instruction}\nReply with ONLY the "
                                "resulting JSON array.")


def parse(arm, text):
    if arm == "PACT":
        return lb.decode(lb.extract_wire(text), lb.REG)["records"]
    t = re.sub(r"```[a-zA-Z]*\n?", "", text)
    m = re.search(r"\[[\s\S]*\]", t)
    return json.loads(m.group(0) if m else t)


def inject(arm, text):
    """thickness_mm := 9999 on one emitted row (out of range hi=600). JSON:
    valid JSON -> silent. PACT: range violation -> fail-closed."""
    if arm == "PACT":
        lines = text.split("\n")
        for i, l in enumerate(lines):
            cs = l.split(",")
            if (len(cs) == 6 and not l.startswith(">") and "[" not in l
                    and not l.startswith("#n=") and not l.startswith("^")):
                cs[3] = "9999"
                lines[i] = ",".join(cs)
                return "\n".join(lines), True
        return text, False
    try:
        m = re.search(r"\[[\s\S]*\]", text)
        arr = json.loads(m.group(0))
        if arr:
            arr[0]["thickness_mm"] = 9999
            return text.replace(m.group(0),
                                json.dumps(arr, separators=(",", ":"))), True
    except Exception:
        pass
    return text, False


def pipeline(prov, arm, R0, k, p, rng):
    records = R0
    detected = silent = 0
    for h in range(k - 1):
        instr = f"From the records, {FILTERS[h][0]}. Drop all others."
        text = emit(prov, arm, records, instr)
        did = False
        if rng.random() < p:
            text, did = inject(arm, text)
        try:
            recs = parse(arm, text)
            if did and arm == "JSON":
                silent += 1                       # corruption passed unchecked
        except Exception:
            if did and arm == "PACT":
                detected += 1                     # fail-closed caught it
                try:                              # retry the hop clean
                    recs = parse(arm, emit(prov, arm, records, instr))
                except Exception:
                    recs = [r for r in records if FILTERS[h][1](r)]
            else:
                recs = [r for r in records if FILTERS[h][1](r)]
        records = recs
    ans_text = emit(prov, arm, records,
                    "Count the records and reply with ONLY the integer.")
    m = re.search(r"-?\d+", ans_text.strip().split("\n")[-1] or ans_text)
    ans = int(m.group(0)) if m else -1
    return ans, detected, silent


def depth(prov, n, p):
    out = {}
    for k in KS:
        jc = pc = 0
        j_sil = p_det = p_sil = 0
        for i in range(n):
            R0 = base(i)
            tr = truth(R0, k)
            ja, jd, js = pipeline(prov, "JSON", R0, k, p, random.Random(100 + i))
            pa, pd, ps = pipeline(prov, "PACT", R0, k, p, random.Random(100 + i))
            jc += (ja == tr); pc += (pa == tr)
            j_sil += js; p_det += pd; p_sil += ps
        out[str(k)] = {"json_acc": round(jc / n, 3), "pact_acc": round(pc / n, 3),
                       "json_silent": j_sil, "pact_detected": p_det,
                       "pact_silent": p_sil, "n": n}
        print(f"    k={k}: JSON {jc}/{n} (silent {j_sil}) | "
              f"PACT {pc}/{n} (detected {p_det}, silent {p_sil})")
    return out


def agg(runs):
    o = {}
    for k in runs[0]:
        keys = runs[0][k]
        o[k] = {m: round(sum(r[k][m] for r in runs) / len(runs), 3) for m in keys}
    return o


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="")
    ap.add_argument("--n", type=int, default=10, help="base cases per depth")
    ap.add_argument("--p", type=float, default=0.2, help="per-hop corruption prob")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    lb.load_dotenv()

    if args.models:
        specs = [s.strip() for s in args.models.split(",") if s.strip()]
    else:
        specs = []
        if os.environ.get("ANTHROPIC_API_KEY"):
            specs.append("anthropic:claude-haiku-4-5")
        if os.environ.get("OPENROUTER_API_KEY"):
            specs.append("openrouter:openai/gpt-4o-mini")
        if os.environ.get("ANTHROPIC_API_KEY"):
            specs.append("anthropic:claude-sonnet-4-6")

    def reps_for(spec):
        return 1 if "sonnet" in spec else 2
    per_pipe = sum(KS)   # ~k calls/pipeline summed over depths
    plan = {s: args.n * per_pipe * 2 * reps_for(s) for s in specs}
    print(f"models: {specs}")
    print(f"E9 depth k in {KS}, n={args.n}, p={args.p}; repeats haiku/gpt=2, "
          "sonnet=1")
    print(f"planned calls/model (approx, + PACT retries): {plan}")
    if args.dry_run:
        return

    e9 = {}
    for spec in specs:
        print(f"\n== {spec} (repeats={reps_for(spec)}) ==")
        prov = lb.Provider(spec, use_cache=not args.no_cache)
        runs = []
        for r in range(reps_for(spec)):
            lb.REPEAT = r
            if reps_for(spec) > 1:
                print(f"  -- repeat {r + 1}/{reps_for(spec)} --")
            runs.append(depth(prov, args.n, args.p))
        e9[spec] = {"depth": agg(runs), "repeats": reps_for(spec),
                    "api_calls": prov.calls, "cache_hits": prov.cache_hits}

    os.makedirs(RESULTS, exist_ok=True)
    json.dump(e9, open(os.path.join(RESULTS, "e9_depth.json"), "w"), indent=1)
    write_report(e9, args)
    make_fig(e9)
    print("\nwritten: results/E9_DEPTH.md + results/e9_depth.json + "
          "figures/fig7_depth.png")


def write_report(e9, args):
    short = {"anthropic:claude-haiku-4-5": "haiku-4-5",
             "openrouter:openai/gpt-4o-mini": "gpt-4o-mini",
             "anthropic:claude-sonnet-4-6": "sonnet-4-6"}
    L = ["# E9 — depth divergence (fail-closed compounding)", "",
         f"k-hop transforming pipeline (filter chain -> count), per-hop "
         f"structural corruption p={args.p} (thickness:=9999, out of range). "
         "JSON accepts it silently; PACT catches it fail-closed and retries. "
         "temperature=0; repeats haiku/gpt=2, sonnet=1.", "",
         "Pre-registered null: if JSON accuracy does not fall with k, no "
         "divergence is claimed.", ""]
    for spec, e in e9.items():
        d = e["depth"]
        L += [f"## {short.get(spec, spec)}", "",
              "| k | JSON acc | PACT acc | JSON silent corruptions | "
              "PACT detected+retried | PACT silent |", "|---|---|---|---|---|---|"]
        for k in ("2", "4", "6", "8"):
            r = d[k]
            L.append(f"| {k} | {r['json_acc']} | {r['pact_acc']} "
                     f"| {r['json_silent']} | {r['pact_detected']} "
                     f"| {r['pact_silent']} |")
        j2, j8 = d["2"]["json_acc"], d["8"]["json_acc"]
        verdict = ("JSON degrades with depth" if j8 < j2 - 0.05
                   else "NULL: JSON did not degrade with depth (reported as-is)")
        L += ["", f"**{short.get(spec, spec)}: {verdict}** "
              f"(JSON {j2}->{j8} from k=2->8; PACT "
              f"{d['2']['pact_acc']}->{d['8']['pact_acc']}).", ""]
    open(os.path.join(RESULTS, "E9_DEPTH.md"), "w", encoding="utf-8").write(
        "\n".join(L))


def make_fig(e9):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        os.makedirs(FIG, exist_ok=True)
        short = {"anthropic:claude-haiku-4-5": "haiku-4-5",
                 "openrouter:openai/gpt-4o-mini": "gpt-4o-mini",
                 "anthropic:claude-sonnet-4-6": "sonnet-4-6"}
        col = {"anthropic:claude-haiku-4-5": "#0EA5A4",
               "openrouter:openai/gpt-4o-mini": "#F59E0B",
               "anthropic:claude-sonnet-4-6": "#6366F1"}
        fig, ax = plt.subplots(figsize=(6.6, 4.0), dpi=200)
        xs = [2, 4, 6, 8]
        for spec, e in e9.items():
            d = e["depth"]
            j = [d[str(k)]["json_acc"] for k in xs]
            p = [d[str(k)]["pact_acc"] for k in xs]
            ax.plot(xs, j, "--o", color=col.get(spec, "#888"),
                    label=f"{short.get(spec, spec)} JSON", alpha=0.9)
            ax.plot(xs, p, "-s", color=col.get(spec, "#888"),
                    label=f"{short.get(spec, spec)} PACT", alpha=0.9)
        ax.set_xlabel("pipeline depth k (hops)")
        ax.set_ylabel("final-task accuracy")
        ax.set_xticks(xs)
        ax.set_ylim(0, 1.05)
        ax.set_title("Figure 7 - Fail-closed compounding: accuracy vs pipeline "
                     "depth\n(dashed=JSON silent-corrupt, solid=PACT fail-closed)",
                     fontsize=10, loc="left")
        ax.legend(frameon=False, fontsize=7, ncol=3, loc="lower left")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        fig.savefig(os.path.join(FIG, "fig7_depth.png"))
    except Exception as e:
        print(f"(figure skipped: {type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
