#!/usr/bin/env python3
"""E8 — cost-per-correct-task (CPCT), pure accounting over existing results.
Zero API calls. Combines L1/L3 accuracy + real per-format usage tokens (from
results/echo_off.json + echo_on.json) + provider pricing into the headline
metric and the Pareto figure (cost-per-correct vs accuracy, format x mode x
model). Lower CPCT + higher accuracy = better (bottom-right is Pareto-optimal).

    python3 experiments/e8_cpct.py
"""
import os
import json
import math

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
RESULTS = os.path.join(ROOT, "results")
FIG = os.path.join(RESULTS, "figures")

# (input $/M, output $/M) — July-2026 list rates.
PRICE = {
    "claude-haiku-4-5": (1.0, 5.0),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "claude-sonnet-4-6": (3.0, 15.0),
}
SHORT = {"claude-haiku-4-5": "haiku-4-5",
         "openai/gpt-4o-mini": "gpt-4o-mini",
         "claude-sonnet-4-6": "sonnet-4-6"}


def cost_usd(d, model):
    ip, op = PRICE[model]
    cached = d.get("cached_in_tokens", 0)
    return (d["in_tokens"] * ip + cached * 0.10 * ip + d["out_tokens"] * op) / 1e6


def cpct(cost, accuracy, n):
    correct = accuracy * n
    return cost / correct if correct > 0 else math.inf


def main():
    off = json.load(open(os.path.join(RESULTS, "echo_off.json")))["models"]
    on = json.load(open(os.path.join(RESULTS, "echo_on.json")))["models"]
    rows = []   # (spec, exp, mode, accuracy, cost_usd, cpct)
    for spec in off:
        model = spec.split(":", 1)[1]
        if model not in PRICE:
            continue
        # L1 — raw reading; JSON/TOON echo-invariant, PACT off vs on
        l1o, l1n = off[spec]["L1_comprehension"], on[spec]["L1_comprehension"]
        for mode, d in (("JSON", l1o["JSON"]), ("TOON", l1o["TOON"]),
                        ("PACT-raw (no echo)", l1o["PACT"]),
                        ("PACT-raw (echo)", l1n["PACT"])):
            c = cost_usd(d, model)
            rows.append((spec, "L1", mode, d["accuracy"], c,
                         cpct(c, d["accuracy"], d["n"])))
        # L3 — end-task; PACT and PACT-decoded, echo off vs on
        l3o, l3n = off[spec]["L3_two_hop"], on[spec]["L3_two_hop"]
        for mode, d in (("JSON", l3o["JSON"]), ("TOON", l3o["TOON"]),
                        ("PACT (no echo)", l3o["PACT"]),
                        ("PACT-decoded (no echo)", l3o["PACT-decoded"]),
                        ("PACT (echo)", l3n["PACT"]),
                        ("PACT-decoded (echo)", l3n["PACT-decoded"])):
            a = d["end_task_accuracy"]
            c = cost_usd(d, model)
            rows.append((spec, "L3", mode, a, c, cpct(c, a, d["n"])))

    # ---- report ----
    lines = ["# E8 — cost-per-correct-task (CPCT)", "",
             "Pure accounting over L1/L3 accuracy + real per-format usage tokens "
             "+ list pricing (input, output $/M; cached reads at 0.1x). "
             "CPCT = task cost / correct tasks; lower is better. `inf` = the "
             "mode got zero correct, so no cost-per-correct is defined.", ""]
    for exp in ("L1", "L3"):
        lines += [f"## {exp} — CPCT (cents per correct task) and accuracy", "",
                  "| model | mode | accuracy | $/task | cents/correct |",
                  "|---|---|---|---|---|"]
        for (spec, e, mode, a, c, k) in rows:
            if e != exp:
                continue
            kc = "inf" if math.isinf(k) else f"{k * 100:.4f}"
            lines.append(f"| {SHORT[spec.split(':',1)[1]]} | {mode} | {a:.3f} "
                         f"| {c:.6f} | {kc} |")
        lines.append("")
    open(os.path.join(RESULTS, "E8_CPCT.md"), "w", encoding="utf-8").write(
        "\n".join(lines))
    json.dump([{"spec": s, "exp": e, "mode": m, "accuracy": a,
                "cost_usd": c, "cpct": (None if math.isinf(k) else k)}
               for (s, e, m, a, c, k) in rows],
              open(os.path.join(RESULTS, "e8_cpct.json"), "w"), indent=1)

    # ---- Pareto figure ----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.lines as mlines
        os.makedirs(FIG, exist_ok=True)
        colors = {"claude-haiku-4-5": "#0EA5A4",
                  "openai/gpt-4o-mini": "#F59E0B",
                  "claude-sonnet-4-6": "#6366F1"}
        fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), dpi=200)
        for ax, exp in zip(axes, ("L1", "L3")):
            for (spec, e, mode, a, c, k) in rows:
                if e != exp or math.isinf(k):
                    continue
                model = spec.split(":", 1)[1]
                pact = "PACT" in mode
                ax.scatter(a, k * 100, s=70, color=colors[model],
                           marker=("s" if pact else "o"),
                           edgecolor="white", linewidth=0.6, alpha=0.9, zorder=3)
                if pact and "decoded" in mode:
                    ax.annotate("dec", (a, k * 100), fontsize=6,
                                xytext=(3, 3), textcoords="offset points")
            ax.set_xlabel("accuracy (higher better)")
            ax.set_ylabel("cents per correct task (lower better)")
            ax.set_title(f"{exp}: cost-per-correct vs accuracy", fontsize=11,
                         loc="left")
            ax.set_yscale("log")
            ax.spines[["top", "right"]].set_visible(False)
        handles = [mlines.Line2D([], [], color=c, marker="o", linestyle="",
                                 label=SHORT[m]) for m, c in colors.items()]
        handles += [mlines.Line2D([], [], color="#555", marker="s",
                                  linestyle="", label="PACT (square)"),
                    mlines.Line2D([], [], color="#555", marker="o",
                                  linestyle="", label="JSON/TOON (circle)")]
        axes[1].legend(handles=handles, frameon=False, fontsize=8,
                       loc="upper right")
        fig.suptitle("Figure 6 - Pareto: cost-per-correct-task vs accuracy "
                     "(bottom-right is best)", fontsize=12, x=0.01, ha="left")
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        fig.savefig(os.path.join(FIG, "fig6_pareto.png"))
        fig_note = "figures/fig6_pareto.png"
    except Exception as e:
        fig_note = f"(figure skipped: {type(e).__name__}: {e})"

    print("\n".join(lines))
    print(f"\nwritten: results/E8_CPCT.md + results/e8_cpct.json + {fig_note}")


if __name__ == "__main__":
    main()
