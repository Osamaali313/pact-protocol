#!/usr/bin/env python3
"""Generate the live-benchmark dataset: large seeded record tables + QA pairs
with programmatically computed ground truth. Deterministic (seed 2026) —
regenerating always yields the identical dataset, so results are comparable
across machines and runs.

Writes experiments/data/{walls,tasks,logs}.json  (2,400 records total)
       experiments/data/qa.json                  (comprehension + 2-hop QA)
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from pact import Schema, Field  # noqa: E402

random.seed(2026)
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

LEVELS = ("L01", "L02", "L03", "L04", "ROOF")
RATINGS = ("EI30", "EI60", "EI90", "EI120", "NR")

WALLS = Schema("walls", (
    Field("id", "str"),
    Field("level", "enum", LEVELS),
    Field("fire_rating", "enum", RATINGS),
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


def gen_walls(n=1000):
    out = []
    for i in range(n):
        out.append({
            "id": f"W-{1000 + i}",
            "level": random.choice(LEVELS),
            "fire_rating": random.choice(RATINGS),
            "thickness_mm": random.randrange(50, 601, 5),
            "load_bearing": random.random() < 0.4,
            "confidence": round(random.uniform(0.5, 1.0), 3),
        })
    return out


def gen_tasks(n=600):
    a = ("planner", "extractor", "verifier", "writer")
    return [{
        "task_id": f"T-{i:04d}",
        "assignee": random.choice(a),
        "priority": random.choice(("P0", "P1", "P2", "P3")),
        "status": random.choice(("open", "running", "done", "failed")),
        "est_minutes": random.randrange(0, 481),
    } for i in range(n)]


def gen_logs(n=800):
    msgs = ["retrieval hit, 12 chunks", "conflict: amber trust, rule R7",
            "IFC entity missing GlobalId, skipping",
            "gate G2 passed, promoting to silver",
            "tool call failed, retry 2/3 with backoff",
            "schema mismatch: expected walls got slabs",
            "dedup: 3 rows collapsed", "provenance link verified"]
    a = ("planner", "extractor", "verifier", "writer")
    return [{
        "ts": f"2026-07-{1 + i % 28:02d}T{i % 24:02d}:{(i * 7) % 60:02d}:{(i * 13) % 60:02d}Z",
        "agent": random.choice(a),
        "severity": random.choice(("debug", "info", "warn", "error")),
        "message": random.choice(msgs),
        "latency_ms": round(random.uniform(1, 4000), 1),
    } for i in range(n)]


# ------------------------------------------------------------- QA builder ---

def sample_slice(recs, k):
    """Deterministic contiguous slice — same rows in every serialization."""
    start = random.randrange(0, len(recs) - k)
    return recs[start:start + k]


def walls_questions(recs, n_slices=40, rows_per_slice=40):
    qa = []
    for _ in range(n_slices):
        sl = sample_slice(recs, rows_per_slice)
        lvl = random.choice(LEVELS)
        rat = random.choice(RATINGS)
        thr = random.randrange(150, 500, 50)
        kind = random.choice(["count", "argmax", "avg", "count_thr"])
        if kind == "count":
            truth = sum(1 for r in sl if r["level"] == lvl and r["fire_rating"] == rat)
            q = (f"How many walls are on level {lvl} with fire_rating {rat}? "
                 "Answer with a single integer only.")
        elif kind == "argmax":
            cands = [r for r in sl if r["level"] == lvl] or sl
            best = max(cands, key=lambda r: (r["thickness_mm"], r["id"]))
            truth = best["id"]
            scope = f"on level {lvl}" if any(r["level"] == lvl for r in sl) else "overall"
            q = (f"Which wall {scope} has the greatest thickness_mm "
                 "(break ties by lexicographically greatest id)? "
                 "Answer with the id only, e.g. W-1234.")
        elif kind == "avg":
            cands = [r for r in sl if r["load_bearing"]]
            if not cands:
                continue
            truth = f'{sum(r["confidence"] for r in cands) / len(cands):.2f}'
            q = ("What is the mean confidence of load_bearing walls, rounded "
                 "to 2 decimals? Answer with the number only, e.g. 0.78.")
        else:
            truth = sum(1 for r in sl if r["thickness_mm"] > thr)
            q = (f"How many walls have thickness_mm strictly greater than {thr}? "
                 "Answer with a single integer only.")
        qa.append({"table": "walls", "rows": sl, "question": q,
                   "truth": str(truth), "kind": kind})
    return qa


def hop_questions(recs, n=24, rows_per_slice=60):
    """Two-hop items: hop1 filters/emits a sub-table; hop2 aggregates over
    ONLY the hop-1 payload. Ground truth computed over the true filter."""
    qa = []
    for _ in range(n):
        sl = sample_slice(recs, rows_per_slice)
        lvl = random.choice(LEVELS)
        rat = random.choice(RATINGS)
        thr = random.randrange(150, 500, 50)
        subset = [r for r in sl if r["level"] == lvl]
        if len(subset) < 3:
            continue
        truth = sum(1 for r in subset
                    if r["fire_rating"] == rat and r["thickness_mm"] > thr)
        qa.append({
            "table": "walls", "rows": sl, "level": lvl,
            "hop2_question": (f"Considering ONLY the payload above: how many "
                              f"records have fire_rating {rat} AND "
                              f"thickness_mm strictly greater than {thr}? "
                              "Answer with a single integer only."),
            "truth": str(truth),
        })
    return qa


def main():
    os.makedirs(DATA, exist_ok=True)
    walls, tasks, logs = gen_walls(), gen_tasks(), gen_logs()
    json.dump(walls, open(os.path.join(DATA, "walls.json"), "w"))
    json.dump(tasks, open(os.path.join(DATA, "tasks.json"), "w"))
    json.dump(logs, open(os.path.join(DATA, "logs.json"), "w"))
    qa = {"comprehension": walls_questions(walls),
          "two_hop": hop_questions(walls)}
    json.dump(qa, open(os.path.join(DATA, "qa.json"), "w"), indent=1)
    print(f"walls={len(walls)} tasks={len(tasks)} logs={len(logs)} "
          f"comprehension_qa={len(qa['comprehension'])} "
          f"two_hop_qa={len(qa['two_hop'])}")


if __name__ == "__main__":
    main()
