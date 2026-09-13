#!/usr/bin/env python3
"""Generate publication figures into results/figures/ from results.json."""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "results")
FIG = os.path.join(RES, "figures")
os.makedirs(FIG, exist_ok=True)
res = json.load(open(os.path.join(RES, "results.json")))

TEAL, INK, AMBER, GREY, RED = "#0F766E", "#0B1220", "#D97706", "#94A3B8", "#B91C1C"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "axes.edgecolor": GREY, "axes.linewidth": 0.8})


# fig 1 — tokens per message ------------------------------------------------
def fig_tokens():
    data = res["E1_tokens"]
    fmts = ["JSON pretty", "JSON compact", "YAML", "XML", "TOON", "PACT"]
    colors = ["#CBD5E1", "#94A3B8", "#64748B", "#475569", AMBER, TEAL]
    works = list(data.keys())
    fig, ax = plt.subplots(figsize=(8.2, 3.6), dpi=200)
    w = 0.13
    for i, f in enumerate(fmts):
        xs = [j + (i - 2.5) * w for j in range(len(works))]
        ys = [data[wk][f] for wk in works]
        ax.bar(xs, ys, width=w, label=f, color=colors[i],
               edgecolor="white", linewidth=0.4)
    ax.set_xticks(range(len(works)))
    ax.set_xticklabels([wk.replace(" ", "\n", 1) for wk in works], fontsize=8.5)
    ax.set_ylabel("tokens per message (GPT-2 BPE, exact)")
    ax.set_title("Figure 1 — Token cost per message across four agent "
                 "workloads", fontsize=11, loc="left", pad=10)
    ax.legend(ncol=6, fontsize=7.5, frameon=False, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig1_tokens.png"))


# fig 2 — session cost ------------------------------------------------------
def fig_cost():
    c = res["E2_session_cost"]
    fig, ax = plt.subplots(figsize=(6.8, 3.4), dpi=200)
    styles = {"JSON pretty": ("#94A3B8", "--"), "JSON compact": ("#475569", "--"),
              "TOON": (AMBER, "-"), "PACT": (TEAL, "-")}
    for fmt, (col, ls) in styles.items():
        ax.plot(c["messages"], [v * 100 for v in c[fmt]], color=col, ls=ls,
                lw=2, label=fmt)
    ax.set_xlabel("messages in session (uniform_large workload)")
    ax.set_ylabel("cumulative input cost (¢)")
    p = c["pricing"]
    ax.set_title("Figure 2 — Session cost: schema off-wire + prompt-cache "
                 f"pricing (${p['fresh_input_per_M']}/M fresh, "
                 f"${p['cached_read_per_M']}/M cached)", fontsize=10,
                 loc="left", pad=10)
    ax.legend(frameon=False, fontsize=8.5)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig2_session_cost.png"))


# fig 3 — architecture ------------------------------------------------------
def _box(ax, x, y, w, h, text, fc, tc="white", fs=9.5, ec="none"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012",
                                fc=fc, ec=ec, lw=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=tc, wrap=True)


def fig_arch():
    fig, ax = plt.subplots(figsize=(7.6, 4.6), dpi=200)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    _box(ax, 0.4, 8.6, 9.2, 1.0,
         "Consumers — agent logic · human bridge ·\ndocument ingestion (PDF / IFC / CSV)", "#0B1220", fs=8.5)
    _box(ax, 0.4, 6.9, 9.2, 1.2,
         "L3 · Constrained Generation Contract\ngrammar from schema → replies valid-by-construction", TEAL, fs=8.5)
    _box(ax, 0.4, 5.2, 9.2, 1.2,
         "L2 · Wire Encoding\nvalues-only rows · performatives · sid · ^provenance", "#115E59", fs=8.5)
    _box(ax, 0.4, 3.5, 9.2, 1.2,
         "L1 · Schema Negotiation (once per session)\ntypes · enums · ranges · sid=sha256 · cached prefix", "#134E4A", fs=8.5)
    _box(ax, 0.4, 1.8, 9.2, 1.2,
         "L0 · Existing Transport (unchanged)\nMCP · A2A · ACP · HTTP · message bus", "#334155", fs=8.5)
    for y in (3.0, 4.7, 6.4, 8.1):
        ax.add_patch(FancyArrowPatch((5, y), (5, y + 0.5),
                                     arrowstyle="-|>", mutation_scale=14,
                                     color=INK, lw=1.4))
    ax.text(9.75, 5.8, "static → cached (~10× cheaper)", rotation=90,
            fontsize=8, color=AMBER, ha="center", va="center")
    ax.set_title("Figure 3 — PACT layer architecture", fontsize=11,
                 loc="left")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig3_architecture.png"))


# fig 4 — sequence ----------------------------------------------------------
def fig_sequence():
    fig, ax = plt.subplots(figsize=(7.6, 4.8), dpi=200)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    actors = [("Human /\nDocument", 1.0, "#334155"),
              ("Bridge\n(H2A)", 3.4, "#0B1220"),
              ("Agent A\nplanner", 6.0, TEAL),
              ("Agent B\nextractor", 8.8, "#115E59")]
    for name, x, col in actors:
        _box(ax, x - 0.75, 8.9, 1.5, 0.9, name, col, fs=8.5)
        ax.plot([x, x], [0.6, 8.9], color=GREY, lw=1, ls=":")

    def msg(y, x1, x2, text, col=INK, ls="-"):
        ax.add_patch(FancyArrowPatch((x1, y), (x2, y), arrowstyle="-|>",
                                     mutation_scale=11, color=col, lw=1.5,
                                     linestyle=ls))
        ax.text((x1 + x2) / 2, y + 0.16, text, ha="center", fontsize=7.6,
                color=col)

    msg(8.2, 6.0, 8.8, "!schema walls sid=226909cf  (once)", TEAL)
    msg(7.6, 8.8, 6.0, "confirm sid", "#115E59")
    ax.text(7.4, 7.1, "sid pinned in cached prefix of both agents",
            ha="center", fontsize=7.2, color=AMBER, style="italic")
    msg(6.4, 1.0, 3.4, '"fire-rated walls, level 2?"', "#334155")
    msg(5.8, 3.4, 6.0, ">ask … sid=226909cf (NL → PACT)", INK)
    msg(5.0, 6.0, 8.8, ">ask + grammar contract", TEAL)
    ax.text(8.8, 4.4, "constrained decode\n(valid by construction)",
            ha="center", fontsize=7.2, color="#115E59")
    msg(3.8, 8.8, 6.0, ">tell walls[12] rows + ^provenance", "#115E59")
    ax.text(6.0, 3.2, "parse + enum/range check (fail-closed)", ha="center",
            fontsize=7.2, color=INK)
    msg(2.6, 6.0, 3.4, "validated records", INK)
    msg(1.9, 3.4, 1.0, "NL answer + provenance citations", "#334155")
    ax.set_title("Figure 4 — Session lifecycle: negotiation, H2A bridge, "
                 "constrained exchange", fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig4_sequence.png"))


# fig 5 — integrity ---------------------------------------------------------
def fig_integrity():
    d = res["E4_fail_closed"]
    modes = list(d.keys())

    def pv(m):   # PACT detection %: per-profile keys (v0.3); model+echo covers all
        v = d[m]
        for k in ("pact_detect_pct_model_echo", "pact_detect_pct_model",
                  "pact_detect_pct_machine"):
            if v.get(k) is not None:
                return v[k]
        return 0
    fig, ax = plt.subplots(figsize=(6.8, 3.2), dpi=200)
    xs = range(len(modes))
    ax.bar([x - 0.19 for x in xs], [pv(m) for m in modes],
           width=0.38, color=TEAL, label="PACT decode (fail-closed)")
    ax.bar([x + 0.19 for x in xs], [d[m]["json_detect_pct"] for m in modes],
           width=0.38, color="#CBD5E1", label="plain JSON parse")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([m.replace("_", "\n") for m in modes], fontsize=8)
    ax.set_ylabel("corruption detected (%)")
    ax.set_ylim(0, 112)
    ax.set_title("Figure 5 — Message-integrity detection, 200 trials per "
                 "class (E4)", fontsize=11, loc="left", pad=10)
    ax.legend(frameon=False, fontsize=8.5)
    ax.spines[["top", "right"]].set_visible(False)
    for x in xs:
        ax.text(x - 0.19, pv(modes[x]) + 3, "100",
                ha="center", fontsize=8, color=TEAL)
        ax.text(x + 0.19, d[modes[x]]["json_detect_pct"] + 3, "0",
                ha="center", fontsize=8, color="#64748B")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "fig5_integrity.png"))


if __name__ == "__main__":
    fig_tokens(); fig_cost(); fig_arch(); fig_sequence(); fig_integrity()
    print("figures written:", os.listdir(FIG))
