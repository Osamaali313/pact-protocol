#!/usr/bin/env python3
"""PACT live benchmark — runs against REAL provider SDKs with YOUR keys.

Quickstart
----------
    pip install -r requirements.txt          # includes anthropic + openai
    python3 experiments/data_gen.py          # deterministic dataset (once)
    cp .env.example .env                     # put your API keys in .env
    python3 experiments/live_bench.py --dry-run   # shows planned calls, $0
    python3 experiments/live_bench.py             # runs everything it can

Providers are auto-detected from environment variables (a .env file in the
repo root is loaded automatically, no dependency needed):
    ANTHROPIC_API_KEY   -> anthropic:claude-haiku-4-5
    OPENAI_API_KEY      -> openai:gpt-4.1-mini
    OPENROUTER_API_KEY  -> openrouter:openai/gpt-4o-mini
    GROQ_API_KEY        -> groq:llama-3.3-70b-versatile
    DEEPSEEK_API_KEY    -> deepseek:deepseek-chat
    XAI_API_KEY         -> xai:grok-3-mini
    MINIMAX_API_KEY     -> minimax:MiniMax-Text-01
Override with:  --models "anthropic:claude-sonnet-4-6,openai:gpt-4.1"
(Model IDs current as of July 2026 — adjust to whatever your account offers.)

Experiments (per model, per format in {JSON, TOON, PACT}):
    L1 comprehension  same rows serialized 3 ways -> data QA accuracy + tokens
    L2 generation     model must EMIT the format; validity rate, retries,
                      output tokens (PACT via fail-closed ask_pact loop,
                      JSON via json.loads+schema check, TOON via parser)
    L3 two-hop        agent 1 emits a filtered payload, agent 2 answers an
                      aggregate question over ONLY that payload -> end-task
                      accuracy across the hand-off (the H3 mechanism)

Cost control: responses are cached in results/live_cache.jsonl (re-runs are
free); --nq/--nhop/--ngen shrink the suite; --dry-run prices it first.
Output: results/live_results.json + results/LIVE_RESULTS.md (paper-ready).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from pact import Schema, Field, encode, decode, render, PactError  # noqa: E402
from pact.client import session_prompt, extract_wire  # noqa: E402

EXAMPLE_ROWS = [
    {"id": "W-001", "level": "L02", "fire_rating": "EI60",
     "thickness_mm": 200, "load_bearing": True, "confidence": 0.91},
    {"id": "W-002", "level": "ROOF", "fire_rating": "NR",
     "thickness_mm": 120, "load_bearing": False, "confidence": 0.77},
]
PACT_PROMPT_V = "v2"   # set by --pact-prompt
FIELD_ECHO = False     # set by --field-echo (v0.3 inline field echo)
REPEAT = 0             # current repeat index; part of the cache key so each
                       # --repeats iteration is a distinct real call


def pact_system(role_hint=""):
    ex = EXAMPLE_ROWS if PACT_PROMPT_V == "v2" else None
    return session_prompt(WALLS, role_hint, example_rows=ex, field_echo=FIELD_ECHO)
from pact.bench_support import toon_like  # noqa: E402

DATA = os.path.join(HERE, "data")
RESULTS = os.path.join(ROOT, "results")
CACHE_PATH = os.path.join(RESULTS, "live_cache.jsonl")

WALLS = Schema("walls", (
    Field("id", "str"),
    Field("level", "enum", ("L01", "L02", "L03", "L04", "ROOF")),
    Field("fire_rating", "enum", ("EI30", "EI60", "EI90", "EI120", "NR")),
    Field("thickness_mm", "int", lo=50, hi=600),
    Field("load_bearing", "bool"),
    Field("confidence", "float", lo=0.0, hi=1.0),
))
REG = {WALLS.sid: WALLS}
FIELD_ORDER = [f.name for f in WALLS.fields]

# ------------------------------------------ E7: production schema registry ---
# A realistic agent deployment pins the full protocol spec + several schemas in
# its cached prefix. These four extra schemas (+ WALLS) stand in for that
# registry so the cacheable prefix clears the provider minimum (Anthropic
# Haiku 4.5 requires 4096 tokens; below that, cache_control silently no-ops —
# which is why earlier runs reported cached_in=0).

TASKS = Schema("tasks", (
    Field("id", "str"),
    Field("phase", "enum", ("design", "procure", "build", "handover")),
    Field("priority", "enum", ("P0", "P1", "P2", "P3")),
    Field("owner", "str"),
    Field("est_hours", "int", lo=1, hi=2000),
    Field("blocked", "bool"),
))
LOGS = Schema("logs", (
    Field("ts", "str"),
    Field("level", "enum", ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")),
    Field("service", "str"),
    Field("code", "int", lo=0, hi=99999),
    Field("latency_ms", "float", lo=0.0, hi=600000.0),
    Field("retryable", "bool"),
))
DOORS = Schema("doors", (
    Field("id", "str"),
    Field("level", "enum", ("L01", "L02", "L03", "L04", "ROOF")),
    Field("fire_rating", "enum", ("EI30", "EI60", "EI90", "EI120", "NR")),
    Field("width_mm", "int", lo=600, hi=2400),
    Field("egress", "bool"),
    Field("self_close", "bool"),
))
ZONES = Schema("zones", (
    Field("id", "str"),
    Field("use", "enum", ("office", "retail", "plant", "core", "parking")),
    Field("area_m2", "float", lo=1.0, hi=100000.0),
    Field("occupancy", "int", lo=0, hi=10000),
    Field("sprinklered", "bool"),
))
MATERIALS = Schema("materials", (
    Field("id", "str"),
    Field("category", "enum", ("concrete", "steel", "timber", "glass", "gypsum")),
    Field("grade", "str"),
    Field("qty", "float", lo=0.0, hi=1000000.0),
    Field("unit", "enum", ("kg", "m3", "m2", "ea", "m")),
    Field("certified", "bool"),
))
SENSORS = Schema("sensors", (
    Field("id", "str"),
    Field("kind", "enum", ("temp", "smoke", "co2", "occupancy", "vibration")),
    Field("zone", "str"),
    Field("reading", "float", lo=-40.0, hi=100000.0),
    Field("battery_pct", "int", lo=0, hi=100),
    Field("alarm", "bool"),
))
INSPECTIONS = Schema("inspections", (
    Field("id", "str"),
    Field("discipline", "enum", ("fire", "structural", "mep", "envelope", "acc")),
    Field("result", "enum", ("pass", "fail", "conditional", "na")),
    Field("inspector", "str"),
    Field("defects", "int", lo=0, hi=999),
    Field("signed_off", "bool"),
))
RFIS = Schema("rfis", (
    Field("id", "str"),
    Field("status", "enum", ("open", "answered", "closed", "void")),
    Field("discipline", "enum", ("arch", "struct", "mep", "civil", "other")),
    Field("raised_by", "str"),
    Field("age_days", "int", lo=0, hi=3650),
    Field("cost_impact", "bool"),
))
DELIVERIES = Schema("deliveries", (
    Field("id", "str"),
    Field("supplier", "str"),
    Field("status", "enum", ("scheduled", "in_transit", "received", "rejected")),
    Field("pallets", "int", lo=0, hi=5000),
    Field("weight_kg", "float", lo=0.0, hi=500000.0),
    Field("inspected", "bool"),
))
PERMITS = Schema("permits", (
    Field("id", "str"),
    Field("kind", "enum", ("hot_work", "confined", "electrical", "lifting", "dig")),
    Field("status", "enum", ("requested", "active", "expired", "revoked")),
    Field("holder", "str"),
    Field("valid_hours", "int", lo=1, hi=168),
    Field("high_risk", "bool"),
))
INCIDENTS = Schema("incidents", (
    Field("id", "str"),
    Field("severity", "enum", ("near_miss", "minor", "major", "critical")),
    Field("category", "enum", ("fall", "struck", "electrical", "fire", "other")),
    Field("reporter", "str"),
    Field("lost_hours", "float", lo=0.0, hi=100000.0),
    Field("riddor", "bool"),
))
METERS = Schema("meters", (
    Field("id", "str"),
    Field("utility", "enum", ("power", "water", "gas", "heat")),
    Field("zone", "str"),
    Field("value", "float", lo=0.0, hi=100000000.0),
    Field("interval_min", "int", lo=1, hi=1440),
    Field("estimated", "bool"),
))
REGISTRY = {s.name: s for s in (
    WALLS, TASKS, LOGS, DOORS, ZONES, MATERIALS, SENSORS, INSPECTIONS,
    RFIS, DELIVERIES, PERMITS, INCIDENTS, METERS)}

# Per-field semantic docs — what a real deployment pins so agents can reason
# about columns, not just their types. (schema, field) -> one-line meaning.
FIELD_DOCS = {
    ("walls", "id"): "stable element id, unique within the model",
    ("walls", "level"): "storey the wall sits on",
    ("walls", "fire_rating"): "EI resistance class; NR = not rated",
    ("walls", "thickness_mm"): "nominal thickness in millimetres",
    ("walls", "load_bearing"): "true if structural, false if partition",
    ("walls", "confidence"): "extractor confidence 0..1 for this row",
    ("tasks", "id"): "work-package id",
    ("tasks", "phase"): "lifecycle phase of the package",
    ("tasks", "priority"): "P0 highest .. P3 lowest",
    ("tasks", "owner"): "responsible crew or person handle",
    ("tasks", "est_hours"): "estimated remaining effort in hours",
    ("tasks", "blocked"): "true if waiting on a dependency",
    ("logs", "ts"): "ISO-8601 UTC event timestamp",
    ("logs", "level"): "syslog-style severity",
    ("logs", "service"): "emitting service name",
    ("logs", "code"): "application status/error code",
    ("logs", "latency_ms"): "handling latency in milliseconds",
    ("logs", "retryable"): "true if the caller may safely retry",
    ("doors", "id"): "door element id",
    ("doors", "level"): "storey the door sits on",
    ("doors", "fire_rating"): "EI class of the door assembly",
    ("doors", "width_mm"): "clear opening width in millimetres",
    ("doors", "egress"): "true if on a designated escape route",
    ("doors", "self_close"): "true if fitted with a self-closer",
    ("zones", "id"): "fire/occupancy zone id",
    ("zones", "use"): "primary occupancy use",
    ("zones", "area_m2"): "floor area in square metres",
    ("zones", "occupancy"): "design occupant count",
    ("zones", "sprinklered"): "true if sprinkler-protected",
    ("materials", "id"): "line-item id in the bill of materials",
    ("materials", "category"): "material family",
    ("materials", "grade"): "spec grade string (e.g. C40, S355)",
    ("materials", "qty"): "quantity in the stated unit",
    ("materials", "unit"): "unit of measure for qty",
    ("materials", "certified"): "true if cert docs are on file",
    ("sensors", "id"): "device id",
    ("sensors", "kind"): "measured quantity",
    ("sensors", "zone"): "zone id the device reports for",
    ("sensors", "reading"): "latest reading in the device's native unit",
    ("sensors", "battery_pct"): "battery state of charge, percent",
    ("sensors", "alarm"): "true if currently in alarm state",
    ("inspections", "id"): "inspection record id",
    ("inspections", "discipline"): "inspecting discipline; acc = accessibility",
    ("inspections", "result"): "outcome of the inspection",
    ("inspections", "inspector"): "inspector handle",
    ("inspections", "defects"): "count of defects raised",
    ("inspections", "signed_off"): "true if formally signed off",
    ("rfis", "id"): "request-for-information id",
    ("rfis", "status"): "workflow status",
    ("rfis", "discipline"): "owning design discipline",
    ("rfis", "raised_by"): "originator handle",
    ("rfis", "age_days"): "days since raised",
    ("rfis", "cost_impact"): "true if a cost impact is flagged",
    ("deliveries", "id"): "delivery/consignment id",
    ("deliveries", "supplier"): "supplier name",
    ("deliveries", "status"): "logistics status",
    ("deliveries", "pallets"): "pallet count",
    ("deliveries", "weight_kg"): "gross weight in kilograms",
    ("deliveries", "inspected"): "true if goods-in inspection is done",
    ("permits", "id"): "permit-to-work id",
    ("permits", "kind"): "class of permitted work",
    ("permits", "status"): "permit lifecycle status",
    ("permits", "holder"): "responsible permit holder handle",
    ("permits", "valid_hours"): "validity window in hours from issue",
    ("permits", "high_risk"): "true if flagged high-risk activity",
    ("incidents", "id"): "safety incident id",
    ("incidents", "severity"): "severity band",
    ("incidents", "category"): "incident mechanism/category",
    ("incidents", "reporter"): "reporter handle",
    ("incidents", "lost_hours"): "person-hours lost to the incident",
    ("incidents", "riddor"): "true if RIDDOR-reportable",
    ("meters", "id"): "meter device id",
    ("meters", "utility"): "metered utility",
    ("meters", "zone"): "zone id the meter serves",
    ("meters", "value"): "cumulative reading in the utility's native unit",
    ("meters", "interval_min"): "reporting interval in minutes",
    ("meters", "estimated"): "true if the reading is estimated not measured",
}

_PROTOCOL_SPEC = """\
PACT — Prompt-cache-Aware, Constrained, Typed wire protocol (agent runtime spec)

You are one agent in an autonomous A2A/H2A pipeline. Every piece of structured
data on this channel is exchanged as a PACT wire message against the schema
registry pinned below. This entire block — the spec and the registry — is
static and lives in the prompt cache of both agents; only per-turn payloads and
questions arrive after it, so keep it byte-identical across turns.

L1 — Schema negotiation (once per session). Each schema is content-addressed by
a sid: the first 8 hex characters of sha256 over its canonical form
`name@version;field:type{enum}[lo,hi];...`. Because the sid is a hash of the
exact type definition, both agents can verify they are talking about the same
contract without re-sending it. Schemas are negotiated once and thereafter a
message names only the sid. On an unknown or mismatched sid you MUST reply with
a >reject message and request renegotiation — never guess the columns.

L2 — Token-dense wire encoding. A message is exactly:
  >VERB s=<sender> r=<recipient> c=<corr> sid=<8-hex>
  <name>[<row_count>]
  <one comma-separated value row per record, values in schema field order>
  optional trailing ^key=value provenance lines
VERB is one of: tell (assert data) | ask (request) | propose (offer for
confirm) | confirm (accept a proposal) | reject (refuse, with reason). Values
are positional — the k-th value in a row is the k-th field in the schema header,
so no keys, quotes, or braces are ever transmitted. Booleans are the single
characters T or F (never true/false, 1/0, or yes/no). Null is the single
character ~. A literal comma inside a string value is escaped as \\, and a
newline as \\n. This density is the point: measured 58–77% fewer tokens than
pretty-printed JSON and consistently fewer than TOON on the same records.

L3 — Constrained, fail-closed decoding. Every received message is validated
against the named sid's schema and REJECTED on any violation. The six corruption
classes the decoder detects with 100% recall (vs 0% for tolerant JSON parsing):
  1. unknown/hallucinated sid;
  2. row arity wrong — a row does not split into exactly the field count;
  3. enum value outside the declared set;
  4. int/float outside its declared [lo,hi] range;
  5. a boolean literal that is not T or F;
  6. declared row_count in name[N] not equal to the number of value rows.
There is NO tolerant or repair path: a malformed message is dropped, not fixed,
and the sender is asked to resend. Therefore, when you emit a message, the count
you write in name[N] MUST equal the exact number of value rows that follow, and
every value must satisfy its column's type, enum, and range.

Provenance & correlation. c=<corr> threads a request to its reply; echo it on
your response. ^source=, ^model=, ^ts= provenance lines are optional and follow
the value rows. Cost model: the pinned prefix above is served from cache at
~0.1x input price on every turn after the first, so a session amortizes the spec
and registry to near-zero and pays full price only for the per-turn payload.

Output discipline. Reply with ONLY a PACT wire message — no prose, no markdown
code fences, no leading commentary — unless a turn explicitly asks a
natural-language question about a payload, in which case answer in one line.
"""


def _schema_glossary(schema):
    """Documented field list for one schema — the kind of registry entry a real
    deployment pins so agents can reason about columns, not just parse them."""
    lines = [f"Schema `{schema.name}` (sid={schema.sid}) — one row per record; "
             "values positional in this field order:"]
    for i, f in enumerate(schema.fields):
        if f.enum:
            t = "enum{" + "|".join(f.enum) + "}"
        elif f.lo is not None or f.hi is not None:
            t = f"{f.ftype} in [{f.lo},{f.hi}]"
        else:
            t = f.ftype
        doc = FIELD_DOCS.get((schema.name, f.name), "")
        lines.append(f"  {i}. {f.name}: {t}"
                     + (f" — {doc}" if doc else "")
                     + " (rejected fail-closed if out of domain)")
    return "\n".join(lines)


def _worked_example(schema, rows):
    wire = encode(rows, schema, sender="extractor", receiver="planner", corr="ex")
    return (f"Worked {schema.name} example — {schema.name}[{len(rows)}] with "
            f"{len(rows)} row(s), count matches:\n{wire}")


_E7_EXAMPLES = {
    "walls": [{"id": "W-001", "level": "L02", "fire_rating": "EI60",
               "thickness_mm": 200, "load_bearing": True, "confidence": 0.91},
              {"id": "W-002", "level": "ROOF", "fire_rating": "NR",
               "thickness_mm": 120, "load_bearing": False, "confidence": 0.77}],
    "tasks": [{"id": "T-11", "phase": "build", "priority": "P1",
               "owner": "crew-a", "est_hours": 48, "blocked": False}],
    "logs": [{"ts": "2026-07-27T09:00:00Z", "level": "ERROR", "service": "gw",
              "code": 503, "latency_ms": 812.4, "retryable": True}],
    "doors": [{"id": "D-3", "level": "L01", "fire_rating": "EI30",
               "width_mm": 900, "egress": True, "self_close": True}],
    "zones": [{"id": "Z-2", "use": "office", "area_m2": 640.5,
               "occupancy": 64, "sprinklered": True}],
    "materials": [{"id": "M-7", "category": "steel", "grade": "S355",
                   "qty": 12500.0, "unit": "kg", "certified": True}],
    "sensors": [{"id": "S-19", "kind": "smoke", "zone": "Z-2",
                 "reading": 0.03, "battery_pct": 88, "alarm": False}],
    "inspections": [{"id": "I-4", "discipline": "fire", "result": "conditional",
                     "inspector": "jra", "defects": 2, "signed_off": False}],
    "rfis": [{"id": "R-22", "status": "open", "discipline": "struct",
              "raised_by": "arch", "age_days": 9, "cost_impact": True}],
    "deliveries": [{"id": "DL-5", "supplier": "acme-steel",
                    "status": "received", "pallets": 6, "weight_kg": 12500.0,
                    "inspected": True}],
    "permits": [{"id": "P-8", "kind": "hot_work", "status": "active",
                 "holder": "crew-b", "valid_hours": 8, "high_risk": True}],
    "incidents": [{"id": "IN-2", "severity": "near_miss", "category": "fall",
                   "reporter": "hse", "lost_hours": 0.0, "riddor": False}],
    "meters": [{"id": "MT-1", "utility": "power", "zone": "Z-2",
                "value": 48210.5, "interval_min": 15, "estimated": False}],
}


def production_prefix():
    """The static, cache-resident block a real PACT agent deployment pins:
    the full protocol spec plus a registry of ten documented schemas with one
    worked example each. Sized to exceed the provider cache minimum (Anthropic
    Haiku 4.5 requires 4096 tokens — below that cache_control silently no-ops,
    which is exactly why earlier runs measured cached_in=0)."""
    parts = [_PROTOCOL_SPEC, "\n=== Pinned schema registry (agents share this) ===\n"]
    for name, schema in REGISTRY.items():
        parts.append(_schema_glossary(schema))
        parts.append(_worked_example(schema, _E7_EXAMPLES[name]))
        parts.append("")
    return "\n".join(parts)

# ----------------------------------------------------------- .env loading ---

def load_dotenv():
    p = os.path.join(ROOT, ".env")
    if not os.path.exists(p):
        return
    for line in open(p):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ------------------------------------------------------------- providers ----

OPENAI_COMPAT = {
    "openai":     (None, "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq":       ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "deepseek":   ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "xai":        ("https://api.x.ai/v1", "XAI_API_KEY"),
    "minimax":    ("https://api.minimax.io/v1", "MINIMAX_API_KEY"),
}
# Newer Anthropic tiers REJECT an explicit temperature (HTTP 400); for those we
# omit it and rely on the model default. The models this harness uses
# (haiku-4-5, sonnet-4-6, gpt-4o-mini) all accept temperature=0.
_NO_TEMPERATURE = ("sonnet-5", "opus-4-7", "opus-4-8", "fable-5", "mythos")


def _temp_ok(model: str) -> bool:
    return not any(s in model for s in _NO_TEMPERATURE)


DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4.1-mini",
    "openrouter": "openai/gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
    "xai": "grok-3-mini",
    "minimax": "MiniMax-Text-01",
}


class Provider:
    """Unified complete(system, user) -> (text, usage) with disk cache."""

    def __init__(self, spec: str, use_cache=True):
        self.spec = spec
        self.kind, _, self.model = spec.partition(":")
        self.use_cache = use_cache
        self.calls = 0
        self.cache_hits = 0
        self.usage = {"in": 0, "out": 0, "cached_in": 0, "cache_creation": 0}
        self._index = None   # lazily-built {key: row} cache index (O(1) lookup)
        if self.kind == "mock":
            self._client = None
        elif self.kind == "anthropic":
            import anthropic
            self._client = anthropic.Anthropic()
        elif self.kind in OPENAI_COMPAT:
            from openai import OpenAI
            base, key_env = OPENAI_COMPAT[self.kind]
            self._client = OpenAI(base_url=base,
                                  api_key=os.environ[key_env])
        else:
            raise SystemExit(f"unknown provider kind: {self.kind}")

    # -- cache --
    def _key(self, system, user, repeat=0):
        # repeat is part of the key so each repeat is its own cached real call:
        # at temperature=0 the prompt is identical, so repeats measure the
        # provider's residual (non-)determinism, and re-runs stay free.
        return hashlib.sha256(
            f"{self.spec}\x00{repeat}\x00{system}\x00{user}".encode()).hexdigest()

    def _load_index(self):
        # Read the whole cache ONCE into {key: row}; later per-key hits are O(1)
        # (the old per-call linear scan is O(calls x cache-lines)).
        self._index = {}
        if os.path.exists(CACHE_PATH):
            for line in open(CACHE_PATH):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._index[row.get("key")] = row

    def _cache_get(self, key):
        if not self.use_cache:
            return None
        if self._index is None:
            self._load_index()
        return self._index.get(key)

    def _cache_put(self, key, text, usage, system="", user=""):
        os.makedirs(RESULTS, exist_ok=True)
        if self._index is not None:
            self._index[key] = {"key": key, "spec": self.spec, "text": text,
                                "usage": usage}
        with open(CACHE_PATH, "a") as f:
            f.write(json.dumps({"key": key, "spec": self.spec,
                                "sys": system[:400], "user": user[:600],
                                "text": text, "usage": usage}) + "\n")

    # -- calls --
    def complete(self, system: str, user: str, max_tokens=1200, repeat=None) -> str:
        if repeat is None:
            repeat = REPEAT
        key = self._key(system, user, repeat)
        hit = self._cache_get(key)
        if hit:
            self.cache_hits += 1
            self._tally(hit["usage"])
            return hit["text"]
        self.calls += 1
        for attempt in range(4):
            try:
                text, usage = self._raw(system, user, max_tokens)
                break
            except Exception as e:  # rate limits / transient
                if attempt == 3:
                    raise
                wait = 2 ** attempt * 2
                print(f"    [{self.spec}] {type(e).__name__}: retrying in {wait}s")
                time.sleep(wait)
        self._tally(usage)
        self._cache_put(key, text, usage, system, user)
        return text

    def _tally(self, u):
        for k in self.usage:
            self.usage[k] += u.get(k, 0)

    def _raw(self, system, user, max_tokens):
        if self.kind == "mock":
            return _mock_model(system, user), {"in": len((system + user).split()),
                                               "out": 40, "cached_in": 0}
        if self.kind == "anthropic":
            kw = dict(model=self.model, max_tokens=max_tokens,
                      system=[{"type": "text", "text": system,
                               "cache_control": {"type": "ephemeral"}}],
                      messages=[{"role": "user", "content": user}])
            if _temp_ok(self.model):
                kw["temperature"] = 0
            r = self._client.messages.create(**kw)
            text = "".join(b.text for b in r.content if b.type == "text")
            u = r.usage
            # Anthropic: input_tokens is the UNCACHED remainder; cache_creation
            # is the write (~1.25x), cache_read is the hit (~0.1x).
            usage = {"in": u.input_tokens, "out": u.output_tokens,
                     "cached_in": getattr(u, "cache_read_input_tokens", 0) or 0,
                     "cache_creation":
                         getattr(u, "cache_creation_input_tokens", 0) or 0}
            return text, usage
        kw = dict(model=self.model, max_tokens=max_tokens,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": user}])
        if _temp_ok(self.model):
            kw["temperature"] = 0
        r = self._client.chat.completions.create(**kw)
        text = r.choices[0].message.content or ""
        u = r.usage
        cached = 0
        det = getattr(u, "prompt_tokens_details", None)
        if det is not None:
            cached = getattr(det, "cached_tokens", 0) or 0
        # OpenAI: prompt_tokens INCLUDES cached_tokens (unlike Anthropic).
        # Normalize to Anthropic's convention: in = uncached remainder.
        return text, {"in": u.prompt_tokens - cached, "out": u.completion_tokens,
                      "cached_in": cached, "cache_creation": 0}

    def raw_usage(self, system, user, max_tokens=300):
        """One LIVE call, bypassing the disk cache. E7 needs real repeated
        calls to exercise the provider's prompt cache — the disk cache would
        short-circuit identical repeats and never hit cache_read."""
        self.calls += 1
        for attempt in range(4):
            try:
                text, usage = self._raw(system, user, max_tokens)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                wait = 2 ** attempt * 2
                print(f"    [{self.spec}] {type(e).__name__}: retrying in {wait}s")
                time.sleep(wait)
        self._tally(usage)
        return text, usage

    def count_prompt_tokens(self, system, user):
        """Exact provider token count of the full prompt (Anthropic only, via
        count_tokens). Returns None for OpenAI-compat (no free counter)."""
        if self.kind == "anthropic":
            r = self._client.messages.count_tokens(
                model=self.model,
                system=[{"type": "text", "text": system}],
                messages=[{"role": "user", "content": user}])
            return r.input_tokens
        return None


# ----------------------------------------------------- mock model (CI only) --

def _mock_model(system: str, user: str) -> str:
    """Deterministic fake model to validate the PIPELINE offline (--models
    mock:pipeline). It answers by parsing the payload itself; ~1 in 6 replies
    is deliberately wrong/corrupt so scoring and retry paths are exercised.
    Its 'results' are meaningless as science — pipeline validation only."""
    noise = int(hashlib.sha256(user.encode()).hexdigest(), 16) % 6 == 0
    if "EMIT-TASK" in user:  # generation task
        # RAW-ROWS is a single JSON line; anything appended (retry suffix or the
        # derived-count note) starts after a blank line.
        rows_json = user.split("RAW-ROWS:", 1)[1].split("\n\n")[0].strip()
        rows = json.loads(rows_json)
        if "as a PACT message" in user:
            wire = encode(rows, WALLS, sender="mock", receiver="bench", corr="g",
                          profile="model")
            if noise and "previous reply violated" not in user:
                wire = wire.replace("L0", "L9", 1)  # corrupt once; retry fixes
            return wire
        if "as TOON" in user:
            return toon_like(rows)
        return json.dumps(rows)
    payload_rows = _extract_rows_any(user)
    q = user
    m = re.search(r"level (\w+) with fire_rating (\w+)", q)
    if "mean confidence" in q:
        c = [r for r in payload_rows if r.get("load_bearing")]
        ans = f'{sum(r["confidence"] for r in c) / len(c):.2f}' if c else "0.00"
    elif "greatest thickness" in q:
        lvl = re.search(r"on level (\w+)", q)
        cand = [r for r in payload_rows if not lvl or r["level"] == lvl.group(1)] or payload_rows
        ans = max(cand, key=lambda r: (r["thickness_mm"], r["id"]))["id"]
    elif m:
        ans = str(sum(1 for r in payload_rows
                      if r["level"] == m.group(1) and r["fire_rating"] == m.group(2)))
    elif "strictly greater than" in q:
        thr = int(re.search(r"greater than (\d+)", q).group(1))
        rat = re.search(r"fire_rating (\w+) AND", q)
        if rat:
            ans = str(sum(1 for r in payload_rows
                          if r["fire_rating"] == rat.group(1)
                          and r["thickness_mm"] > thr))
        else:
            ans = str(sum(1 for r in payload_rows if r["thickness_mm"] > thr))
    elif "Emit ONLY the records on level" in q:
        lvl = re.search(r"level (\w+)", q).group(1)
        sub = [r for r in payload_rows if r["level"] == lvl]
        if noise:
            sub = sub[:-1] if len(sub) > 1 else sub  # drop a row sometimes
        if "PACT message" in q:
            return encode(sub, WALLS, sender="mock", receiver="bench", corr="h",
                          profile="model")
        if "TOON" in q:
            return toon_like(sub) if sub else "items[0]{}:"
        return json.dumps(sub)
    else:
        ans = "0"
    if noise:
        ans = "wrong-" + str(ans)
    return f"The answer is {ans}"


def _extract_rows_any(text: str):
    """Mock helper: recover rows from whichever serialization is embedded."""
    if ">tell" in text or "!schema" in text:
        m = re.search(r">tell[\s\S]*", text)
        if m:
            body = m.group(0).split("\n\nQuestion:")[0].split("\n\nEmit ONLY")[0]
            try:
                return decode(body, REG)["records"]
            except PactError:
                pass
    m = re.search(r"\[\s*{[\s\S]*?}\s*\]", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    rows = []
    body = re.search(r"items\[\d+\]{[^}]*}:\n([\s\S]*?)(\n\n|$)", text)
    if body:
        for line in body.group(1).strip().split("\n"):
            c = line.strip().split(",")
            if len(c) == 6:
                rows.append({"id": c[0], "level": c[1], "fire_rating": c[2],
                             "thickness_mm": int(c[3]),
                             "load_bearing": c[4] == "true",
                             "confidence": float(c[5])})
    return rows


# --------------------------------------------------------- serializations ---

def payload(fmt: str, rows) -> tuple[str, str]:
    """Return (system_prompt, payload_text) for a format. Static instructions
    live in system (cache-aligned for ALL formats — fairness)."""
    if fmt == "PACT":
        sys_p = pact_system("You answer questions about PACT payloads.")
        return sys_p, encode(rows, WALLS, sender="extractor", receiver="bench",
                             corr="q", field_echo=FIELD_ECHO)
    if fmt == "TOON":
        sys_p = ("Data arrives as TOON: 'items[N]{fields}:' then one "
                 "comma-separated value row per record, in field order.")
        return sys_p, toon_like(rows)
    sys_p = "Data arrives as a JSON array of records."
    return sys_p, json.dumps(rows, separators=(",", ":"))


ANS = re.compile(r"(-?\d+\.\d+|-?\d+|W-\d+)")


def score(reply: str, truth: str) -> bool:
    tail = reply.strip().split("\n")[-1]
    hits = ANS.findall(tail) or ANS.findall(reply)
    return bool(hits) and hits[-1] == truth


# ------------------------------------------------------------- experiments --

def _usage_snapshot(prov):
    return (prov.usage["in"], prov.usage["out"], prov.usage["cached_in"])


def _usage_delta(prov, snap):
    return {"in_tokens": prov.usage["in"] - snap[0],
            "out_tokens": prov.usage["out"] - snap[1],
            "cached_in_tokens": prov.usage["cached_in"] - snap[2]}


def l1_comprehension(prov: Provider, qa: list, nq: int):
    out = {}
    for fmt in ("JSON", "TOON", "PACT"):
        ok = 0
        kinds = {}
        snap = _usage_snapshot(prov)   # per-format token attribution (E8 CPCT)
        for item in qa[:nq]:
            sys_p, body = payload(fmt, item["rows"])
            user = f"{body}\n\nQuestion: {item['question']}"
            reply = prov.complete(sys_p, user)
            hit = score(reply, item["truth"])
            ok += hit
            k = kinds.setdefault(item["kind"], [0, 0])
            k[0] += hit; k[1] += 1
        out[fmt] = {"accuracy": round(ok / nq, 3), "n": nq,
                    "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in kinds.items()},
                    **_usage_delta(prov, snap)}
        print(f"    L1 {fmt}: {ok}/{nq}  {out[fmt]['by_kind']}")
    return out


GEN_SYS = {
    "PACT": None,  # session_prompt used
    "JSON": ("Emit data as a JSON array of objects with keys "
             f"{FIELD_ORDER}. Reply with ONLY the JSON array."),
    "TOON": ("Emit data as TOON: first line 'items[N]{" + ",".join(FIELD_ORDER)
             + "}:' then one comma-separated value row per record "
               "(booleans true/false). Reply with ONLY the TOON block."),
}


WIRE_VARIANT = "declared"  # set by --wire-variant

_BODY_DERIVE = re.compile(r"^(\w+)(?:\[[^\]]*\])?$")


def decode_derived(text, registry):
    """RFC-02 A/B variant, harness-side ONLY. Derive the row count from the rows
    actually emitted instead of trusting the model's self-count: rewrite the
    `name[...]` (or bare `name`) marker to the true count, then hand off to the
    strict core `decode`. EVERY other fail-closed check is unchanged — sid,
    per-row arity, enum membership, numeric range, and T/F bool literals all
    still raise PactError. This does NOT touch canonicalization/sid or the wire
    spec in src/pact (Python), sdk-ts, or rust — it is a benchmark probe for
    whether removing the self-count gate recovers the over-emission failures
    (see docs/RFC-02-derived-row-count.md). Unlike truncate, it drops no rows."""
    wire = extract_wire(text)  # strip fences/prose (blank-line stop), keep rows
    lines = wire.strip().split("\n")
    if len(lines) >= 2:
        m = _BODY_DERIVE.match(lines[1].strip())
        if m:
            rows = [ln for ln in lines[2:]
                    if ln.strip() and not ln.lstrip().startswith("^")]
            lines[1] = f"{m.group(1)}[{len(rows)}]"
            wire = "\n".join(lines)
    return decode(wire, registry)


def _pact_derived_note():
    return ("\n\nWrite the row-count marker as `walls[*]` (a literal asterisk) "
            "rather than a number — the receiver derives the true count from "
            "the rows you emit, so do not count them yourself. Every other rule "
            "(T/F booleans, enums, ranges, one value per field in order) still "
            "applies.")


def _pact_decode(text, truncate):
    """PACT wire -> records, honoring the active --wire-variant."""
    if WIRE_VARIANT == "derived-count":
        return decode_derived(text, REG)["records"]
    return decode(extract_wire(text, truncate=truncate), REG)["records"]


def _validate(fmt, text, expected_rows, truncate=False):
    """Returns (valid, exact) — parseable+schema-valid, and content-exact."""
    try:
        if fmt == "PACT":
            recs = _pact_decode(text, truncate)
        elif fmt == "JSON":
            t = re.sub(r"```[a-zA-Z]*\n?", "", text)
            m = re.search(r"\[[\s\S]*\]", t)
            recs = json.loads(m.group(0) if m else t)
            for r in recs:  # schema check parity with PACT's decode
                assert set(r) == set(FIELD_ORDER)
                assert r["level"] in WALLS.fields[1].enum
                assert r["fire_rating"] in WALLS.fields[2].enum
                assert 50 <= int(r["thickness_mm"]) <= 600
                assert isinstance(r["load_bearing"], bool)
                assert 0.0 <= float(r["confidence"]) <= 1.0
        else:
            recs = _extract_rows_any(text)
            assert recs
    except Exception:
        return False, False
    key = lambda rs: sorted(json.dumps(r, sort_keys=True) for r in rs)
    return True, key(recs) == key(expected_rows)


def l2_generation(prov: Provider, walls: list, ngen: int, max_retries=2,
                  truncate=False, quiet=False):
    import random as rnd
    rnd.seed(7)
    slices = [walls[i:i + 12] for i in
              (rnd.sample(range(len(walls) - 12), ngen))]
    out = {}
    for fmt in ("JSON", "TOON", "PACT"):
        first_ok = valid = exact = retries = 0
        out_tok0 = prov.usage["out"]   # per-format output-token accounting
        for rows in slices:
            sys_p = pact_system() if fmt == "PACT" else GEN_SYS[fmt]
            base = ("EMIT-TASK: re-emit the following records "
                    + ("as a PACT message (tell, s=extractor r=bench)."
                       if fmt == "PACT" else
                       ("as TOON." if fmt == "TOON" else "as JSON."))
                    + "\nRAW-ROWS: " + json.dumps(rows))
            if fmt == "PACT" and WIRE_VARIANT == "derived-count":
                base += _pact_derived_note()
            text = prov.complete(sys_p, base)
            v, e = _validate(fmt, text, rows, truncate=truncate)
            first_ok += v
            attempt = 0
            while not v and attempt < max_retries:
                attempt += 1
                text = prov.complete(sys_p, base +
                                     "\n\nYour previous reply violated the "
                                     "format/schema. Reply again with ONLY a "
                                     "valid message.")
                v, e = _validate(fmt, text, rows, truncate=truncate)
            retries += attempt
            valid += v
            exact += e
        out_tokens = prov.usage["out"] - out_tok0
        out[fmt] = {"first_try_valid": round(first_ok / ngen, 3),
                    "valid_after_retries": round(valid / ngen, 3),
                    "content_exact": round(exact / ngen, 3),
                    "total_retries": retries, "n": ngen,
                    "output_tokens": out_tokens,
                    "output_tokens_per_msg": round(out_tokens / ngen, 1)}
        if not quiet:
            print(f"    L2 {fmt}: first-try {first_ok}/{ngen}, "
                  f"final {valid}/{ngen}, retries {retries}")
    return out


def _keyed(recs):
    # PACT-decoded consumption mode: decode the dense wire, then render the
    # keyed view the model reads (the core render(), style="keyed").
    return render(recs, WALLS, style="keyed")


def l3_two_hop(prov: Provider, qa: list, nhop: int, truncate=False, quiet=False):
    out = {}
    for fmt in ("JSON", "TOON", "PACT", "PACT-decoded"):
        ok = hop1_fail = 0
        snap = _usage_snapshot(prov)   # per-format token attribution (E8 CPCT)
        for item in qa[:nhop]:
            wire_fmt = "PACT" if fmt == "PACT-decoded" else fmt
            sys_p, body = payload(wire_fmt, item["rows"])
            emit_as = {"PACT": "a PACT message (tell)", "TOON": "TOON",
                       "JSON": "a JSON array"}[wire_fmt]
            emit_prompt = (f"{body}\n\nEmit ONLY the records on level "
                           f"{item['level']} as {emit_as}. No prose.")
            if wire_fmt == "PACT" and WIRE_VARIANT == "derived-count":
                emit_prompt += _pact_derived_note()
            hop1 = prov.complete(sys_p, emit_prompt)
            # hand-off validation: PACT fail-closed; JSON parse; TOON parse
            v, _ = _validate(wire_fmt, hop1, _true_subset(item), truncate=truncate)
            if wire_fmt == "PACT" and not v:
                hop1_fail += 1
                continue  # fail-closed: pipeline rejects, counts as miss
            if fmt == "PACT-decoded":
                # the architectural mode: codec decodes the wire, the model
                # reads a keyed rendering — wire stays dense, reading is easy
                recs = _pact_decode(hop1, truncate)
                hop2_payload = _keyed(recs)
                sys2 = "Records are given one per line as key=value pairs."
            else:
                hop2_payload = hop1
                sys2, _ = payload(wire_fmt, item["rows"][:1])
            reply = prov.complete(
                sys2, f"{hop2_payload}\n\n{item['hop2_question']}")
            ok += score(reply, item["truth"])
        out[fmt] = {"end_task_accuracy": round(ok / nhop, 3),
                    "hop1_rejected_fail_closed": hop1_fail, "n": nhop,
                    **_usage_delta(prov, snap)}
        if not quiet:
            print(f"    L3 {fmt}: {ok}/{nhop} (fail-closed rejections: {hop1_fail})")
    return out


def _true_subset(item):
    return [r for r in item["rows"] if r["level"] == item["level"]]


# ---------------------------------------------------- E7: caching on billing --

# Base UNCACHED input price, $/1M tokens (July-2026 list; adjust per account).
# Provider cache multipliers: read ~0.10x, ephemeral write ~1.25x (5-min TTL).
INPUT_PRICE = {
    "claude-haiku-4-5": 1.00,
    "claude-sonnet-5": 2.00,        # intro rate; 3.00 list
    "claude-opus-4-8": 5.00,
    "openai/gpt-4o-mini": 0.15,
    "gpt-4.1-mini": 0.40,
}


def _price(model):
    return INPUT_PRICE.get(model, 1.00)


def e7_caching(prov: Provider):
    """E7 — validate the prompt-cache claim on real billing meters.

    Protocol: send the SAME request twice within the 5-minute TTL, then a third
    with a different user turn but the identical cache-pinned prefix.
      call 1 -> cache_creation > 0, cache_read = 0   (writes the prefix)
      call 2 -> cache_read approx= prefix, cache_creation = 0   (identical repeat)
      call 3 -> cache_read approx= prefix, cache_creation = 0   (new turn, same prefix)
    Reports effective input cost/call under cached-token pricing vs an uncached
    baseline (whole prompt at full price every call — what you pay with no
    cache_control; a JSON-serialized registry would be strictly larger still).
    """
    prefix = production_prefix()
    q_a = ("Emit a PACT tell message (s=extractor r=planner) against the pinned "
           "walls schema with exactly these two rows:\n"
           "W-500,L03,EI90,220,T,0.88\nW-501,ROOF,NR,140,F,0.6")
    q_b = ("Emit a PACT tell message (s=extractor r=planner) against the pinned "
           "tasks schema with exactly one row: id T-99, phase design, priority "
           "P0, owner arch, est_hours 12, not blocked.")
    ntok = prov.count_prompt_tokens(prefix, q_a)
    print(f"    {prov.spec}: pinned prefix ~{ntok} tokens"
          f"{' (>4096 floor OK)' if (ntok or 0) > 4096 else ''}")
    steps = [("1_create", prefix, q_a),
             ("2_repeat", prefix, q_a),
             ("3_new_turn", prefix, q_b)]
    price = _price(prov.model)
    # cache read/write multipliers differ by provider: Anthropic reads at 0.10x
    # and writes at 1.25x (5-min TTL); OpenAI-compat caches read at 0.50x with
    # no separate write premium.
    read_mult, write_mult = (0.10, 1.25) if prov.kind == "anthropic" else (0.50, 1.0)
    recs = []
    for label, sys_p, user in steps:
        _, u = prov.raw_usage(sys_p, user)
        eff = (u["in"] + write_mult * u["cache_creation"]
               + read_mult * u["cached_in"]) * price / 1e6
        total_in = u["in"] + u["cache_creation"] + u["cached_in"]
        base = total_in * price / 1e6
        recs.append({"step": label, "in": u["in"],
                     "cache_creation": u["cache_creation"],
                     "cache_read": u["cached_in"], "out": u["out"],
                     "eff_input_usd": round(eff, 8),
                     "baseline_input_usd": round(base, 8)})
        print(f"    {prov.spec} E7 {label}: in={u['in']} "
              f"write={u['cache_creation']} read={u['cached_in']} out={u['out']}")
    # steady-state saving from the cached repeat calls (2 & 3)
    steady = recs[1:]
    eff_sum = sum(r["eff_input_usd"] for r in steady)
    base_sum = sum(r["baseline_input_usd"] for r in steady)
    saving = round(1 - eff_sum / base_sum, 4) if base_sum else 0.0
    return {"model": prov.model, "prefix_tokens": ntok,
            "input_price_per_mtok": price, "cache_read_pricing": read_mult,
            "cache_write_pricing": write_mult, "calls": recs,
            "steady_state_input_saving": saving}


def write_e7_report(e7):
    lines = ["# E7 — prompt-cache validation on real billing meters", "",
             "Per model: identical request sent twice within the 5-minute TTL, "
             "then a third with a different user turn but the same cache-pinned "
             "prefix. `write` = cache_creation_input_tokens (Anthropic) — OpenAI "
             "has no separate write meter. `read` = cache_read_input_tokens / "
             "prompt_tokens_details.cached_tokens. `in` = uncached remainder.", ""]
    for spec, e in e7.items():
        is_anthropic = spec.startswith("anthropic:")
        read_hits = [c["cache_read"] for c in e["calls"] if c["cache_read"]]
        prefix_desc = (f"{e['prefix_tokens']} tokens"
                       if e["prefix_tokens"] is not None
                       else (f"~{read_hits[0]} tokens (from cache_read; this "
                             "provider has no free token counter)"
                             if read_hits else "n/a"))
        floor_note = (" — Anthropic Haiku 4.5 cache floor is 4096; below it "
                      "`cache_control` silently no-ops, which is why earlier "
                      "runs measured cached_in=0" if is_anthropic else "")
        lines += [f"## {spec}", "",
                  f"Pinned prefix: **{prefix_desc}**{floor_note}. Input price "
                  f"${e['input_price_per_mtok']}/1M; cache read "
                  f"{e['cache_read_pricing']}x, write {e['cache_write_pricing']}x.",
                  "",
                  "| call | in (uncached) | write | read | out | eff input $ | "
                  "uncached baseline $ |",
                  "|---|---|---|---|---|---|---|"]
        for c in e["calls"]:
            lines.append(
                f"| {c['step']} | {c['in']} | {c['cache_creation']} | "
                f"{c['cache_read']} | {c['out']} | {c['eff_input_usd']:.8f} | "
                f"{c['baseline_input_usd']:.8f} |")
        lines += ["",
                  f"**Steady-state input-cost saving (cached repeat calls): "
                  f"{e['steady_state_input_saving']*100:.1f}%** vs sending the "
                  f"same prefix uncached every call.", ""]
    return "\n".join(lines)


# ------------------------------------------- v0.2 trailing-count accuracy ---

_TRAILER_RE = re.compile(r"^#n=(\d+)$")


def trailing_count_stats():
    """Post-emission count accuracy: over every v0.2 model-profile PACT
    emission in the cache (`name[*]`), does the model's trailing `#n=N` equal
    the number of rows it actually emitted? This is the RFC-02 bet — models
    can count what they just streamed — measured, not just assumed.
      correct  -> #n=N matches rows (accepted, fail-closed verified)
      wrong    -> #n=N != rows (rejected fail-closed — truncation/miscount)
      missing  -> name[*] with no #n= (decodes but count_verified=False)"""
    stats = {}
    if not os.path.exists(CACHE_PATH):
        return stats
    for line in open(CACHE_PATH):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = r.get("text", "")
        if "[*]" not in t:
            continue                      # model profile only
        w = extract_wire(t)
        lines = w.strip().split("\n")
        bi = next((i for i, l in enumerate(lines)
                   if l.strip().endswith("[*]")), None)
        if bi is None:
            continue
        rows = [l for l in lines[bi + 1:]
                if l.strip() and not l.startswith("^")
                and not _TRAILER_RE.match(l.strip())]
        trailers = [l for l in lines[bi + 1:] if _TRAILER_RE.match(l.strip())]
        s = stats.setdefault(r.get("spec", "?"),
                             {"correct": 0, "wrong": 0, "missing": 0, "total": 0})
        s["total"] += 1
        if not trailers:
            s["missing"] += 1
        elif int(_TRAILER_RE.match(trailers[0].strip()).group(1)) == len(rows):
            s["correct"] += 1
        else:
            s["wrong"] += 1
    return stats


# ---------------------------------------------- repeats + bootstrap 95% CI ---

def _bootstrap_ci(samples, B=2000, seed=1):
    """(mean, lo, hi) — percentile bootstrap 95% CI of the mean. With one
    sample the CI is degenerate (point)."""
    import random as _rnd
    n = len(samples)
    mean = sum(samples) / n
    if n <= 1:
        return mean, mean, mean
    rng = _rnd.Random(seed)
    means = sorted(sum(samples[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(B))
    return mean, means[int(0.025 * B)], means[int(0.975 * B)]


def _aggregate(runs):
    """Merge N per-repeat result dicts {exp:{fmt:{metric:val}}} into one dict;
    numeric leaves become their mean plus a `<metric>_ci` [lo,hi], non-numeric
    leaves (e.g. by_kind) keep the last repeat's value."""
    agg = {}
    for exp in runs[0]:
        agg[exp] = {}
        for fmt in runs[0][exp]:
            agg[exp][fmt] = {}
            for metric, val in runs[0][exp][fmt].items():
                vals = [runs[r][exp][fmt][metric] for r in range(len(runs))]
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    m, lo, hi = _bootstrap_ci([float(x) for x in vals])
                    agg[exp][fmt][metric] = round(m, 3)
                    agg[exp][fmt][metric + "_ci"] = [round(lo, 3), round(hi, 3)]
                else:
                    agg[exp][fmt][metric] = val
    return agg


# --------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="",
                    help="comma list like anthropic:claude-sonnet-4-6,openai:gpt-4.1-mini")
    ap.add_argument("--nq", type=int, default=24, help="L1 questions/format")
    ap.add_argument("--ngen", type=int, default=10, help="L2 slices/format")
    ap.add_argument("--nhop", type=int, default=12, help="L3 items/format")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--e7", action="store_true",
                    help="run ONLY E7 (prompt-cache validation on real billing "
                         "meters): 3 live, disk-uncached calls per model.")
    ap.add_argument("--pact-prompt", choices=["v1", "v2"], default="v2",
                    help="v2 adds a cached one-shot worked example (~70 "
                         "cached tokens) to the PACT session prompt")
    ap.add_argument("--pact-extract", choices=["strict", "truncate", "both"],
                    default="strict",
                    help="PACT wire extraction for L2/L3. strict (default): "
                         "fail-closed. Under the v0.2 model profile the trailing "
                         "#n= enforces the count, so the legacy truncate delta "
                         "is obsolete; pass 'both' only to compare against v0.1 "
                         "declared-count leniency.")
    ap.add_argument("--wire-variant", choices=["declared", "derived-count"],
                    default="declared",
                    help="declared (default): model writes name[N] and the "
                         "count is a fail-closed gate. derived-count (RFC-02 "
                         "A/B, harness-side only): model writes name[*] and the "
                         "decoder derives N from the rows; all other fail-closed "
                         "checks unchanged. Does not touch sid/canonicalization.")
    ap.add_argument("--repeats", type=int, default=1,
                    help="repeats per model (paper run: 3). Each repeat is a "
                         "distinct real call at temperature=0; tables report "
                         "mean ± bootstrap 95%% CI.")
    ap.add_argument("--field-echo", choices=["off", "on"], default="off",
                    help="v0.3 inline field echo name[*]{fields} in PACT "
                         "payloads and emission prompts (RFC-03).")
    args = ap.parse_args()

    load_dotenv()
    global PACT_PROMPT_V, WIRE_VARIANT, FIELD_ECHO, REPEAT
    PACT_PROMPT_V = args.pact_prompt
    WIRE_VARIANT = args.wire_variant
    FIELD_ECHO = (args.field_echo == "on")
    if args.models:
        specs = [s.strip() for s in args.models.split(",") if s.strip()]
    else:
        specs = []
        if os.environ.get("ANTHROPIC_API_KEY"):
            specs.append(f"anthropic:{DEFAULT_MODELS['anthropic']}")
        for kind, (_, env) in OPENAI_COMPAT.items():
            if os.environ.get(env):
                specs.append(f"{kind}:{DEFAULT_MODELS[kind]}")
    if not specs:
        raise SystemExit(
            "No API keys found. Put keys in .env (see .env.example) or pass "
            "--models. For an offline pipeline check: --models mock:pipeline")

    if args.e7:
        print(f"models: {specs}")
        print("E7: 3 live calls per model (NOT disk-cached — measures the "
              "provider prompt cache). ~$0.00–0.01/model.")
        if args.dry_run:
            return
        e7 = {}
        for spec in specs:
            print(f"\n== {spec} (E7) ==")
            e7[spec] = e7_caching(Provider(spec, use_cache=False))
        os.makedirs(RESULTS, exist_ok=True)
        json.dump(e7, open(os.path.join(RESULTS, "e7_caching.json"), "w"), indent=1)
        open(os.path.join(RESULTS, "E7_CACHING.md"), "w",
             encoding="utf-8").write(write_e7_report(e7))
        print("\nwritten: results/e7_caching.json + results/E7_CACHING.md")
        return

    if not os.path.exists(os.path.join(DATA, "qa.json")):
        raise SystemExit("Dataset missing — run: python3 experiments/data_gen.py")
    qa = json.load(open(os.path.join(DATA, "qa.json")))
    walls = json.load(open(os.path.join(DATA, "walls.json")))

    per_model = (3 * (args.nq + args.ngen) + 4 * args.nhop * 2) * args.repeats
    print(f"models: {specs}")
    print(f"repeats: {args.repeats} | field_echo: {args.field_echo} | "
          f"temperature: 0 (where accepted)")
    print(f"planned LLM calls per model: ~{per_model} "
          f"(+ retries; cached re-runs are free)")
    if args.dry_run:
        return

    ab_derived = (args.wire_variant == "derived-count")
    primary_trunc = (args.pact_extract == "truncate") and not ab_derived
    want_delta = (args.pact_extract == "both") and not ab_derived
    results = {"config": vars(args), "models": {}}
    for spec in specs:
        print(f"\n== {spec} ==")
        prov = Provider(spec, use_cache=not args.no_cache)
        runs = []
        for rep in range(args.repeats):
            REPEAT = rep
            if args.repeats > 1:
                print(f"  -- repeat {rep + 1}/{args.repeats} --")
            runs.append({
                "L1_comprehension": l1_comprehension(prov, qa["comprehension"], args.nq),
                "L2_generation": l2_generation(prov, walls, args.ngen,
                                               truncate=primary_trunc),
                "L3_two_hop": l3_two_hop(prov, qa["two_hop"], args.nhop,
                                         truncate=primary_trunc),
            })
        r = _aggregate(runs)
        r.update({"token_usage": prov.usage, "api_calls": prov.calls,
                  "cache_hits": prov.cache_hits, "repeats": args.repeats})
        if want_delta:
            r["L2_generation_truncate"] = l2_generation(
                prov, walls, args.ngen, truncate=True, quiet=True)
            r["L3_two_hop_truncate"] = l3_two_hop(
                prov, qa["two_hop"], args.nhop, truncate=True, quiet=True)
        if ab_derived:
            WIRE_VARIANT = "declared"
            r["L2_generation_declared"] = l2_generation(
                prov, walls, args.ngen, quiet=True)
            r["L3_two_hop_declared"] = l3_two_hop(
                prov, qa["two_hop"], args.nhop, quiet=True)
            WIRE_VARIANT = "derived-count"
        results["models"][spec] = r

    os.makedirs(RESULTS, exist_ok=True)
    json.dump(results, open(os.path.join(RESULTS, "live_results.json"), "w"),
              indent=1)

    if ab_derived:
        head_note = ("PACT wire variant: **derived-count** (RFC-02 A/B, primary "
                     "columns). The model writes `name[*]` and the decoder "
                     "derives N from the emitted rows; sid, arity, enum, range, "
                     "and T/F bool checks are all unchanged and still fail "
                     "closed. Harness-side only — sid/canonicalization and the "
                     "three implementations are untouched. Declared-count "
                     "baseline and delta below; see docs/RFC-02-derived-row-count.md.")
    else:
        head_note = ("PACT wire profile: **v0.2 model** (`name[*]` + trailing "
                     "`#n=<count>`). The model writes the count AFTER emitting "
                     "rows and the decoder enforces it fail-closed — no "
                     "pre-declared self-count to miss. Decode is strict; a "
                     "wrong `#n=` is rejected. Post-emission count accuracy is "
                     "reported below; see docs/RFC-02-derived-row-count.md.")
    rigor = (f"temperature=0 (where the model accepts it); repeats="
             f"{args.repeats}"
             + (" with mean ± bootstrap 95% CI in [lo,hi]." if args.repeats > 1
                else " (single run — no CI)."))
    lines = ["# PACT live benchmark results", "",
             "One table per experiment; rows are models, provider tokenizers "
             "and billing (usage fields) are authoritative.", "",
             rigor, "", head_note, ""]

    def cell(d, metric):
        v = d[metric]
        ci = d.get(metric + "_ci")
        if args.repeats > 1 and ci:
            return f"{v:.3f} [{ci[0]:.2f},{ci[1]:.2f}]"
        return f"{v}"

    for exp, metric in (("L1_comprehension", "accuracy"),
                        ("L2_generation", "valid_after_retries"),
                        ("L3_two_hop", "end_task_accuracy")):
        fmts = ("JSON", "TOON", "PACT", "PACT-decoded") if exp == "L3_two_hop" \
            else ("JSON", "TOON", "PACT")
        lines += [f"## {exp}", "",
                  "| model | " + " | ".join(fmts) + " |",
                  "|---|" + "---|" * len(fmts)]
        for spec, r in results["models"].items():
            row = r[exp]
            lines.append(f"| {spec} | " + " | ".join(
                cell(row[f], metric) for f in fmts) + " |")
        lines.append("")

    # L2 output-token economics (output ≈ 5× input price): the model-emitted
    # PACT rows should be far cheaper to GENERATE than JSON.
    lines += ["## L2 output tokens per message (lower = cheaper to generate)",
              "", "| model | JSON | TOON | PACT | PACT/JSON |",
              "|---|---|---|---|---|"]
    for spec, r in results["models"].items():
        g = r["L2_generation"]
        j = g["JSON"]["output_tokens_per_msg"]
        p = g["PACT"]["output_tokens_per_msg"]
        ratio = f"{p / j:.2f}" if j else "—"
        lines.append(f"| {spec} | {cell(g['JSON'],'output_tokens_per_msg')} "
                     f"| {cell(g['TOON'],'output_tokens_per_msg')} "
                     f"| {cell(g['PACT'],'output_tokens_per_msg')} | {ratio} |")
    lines.append("")

    if want_delta:
        lines += [
            "## PACT extraction delta (strict → truncate)", "",
            "How much of PACT's apparent validity/accuracy depends on lenient "
            "row-count truncation (silently dropping rows a model over-emits "
            "under a wrong `name[N]` count). `truncate − strict` > 0 means "
            "truncation is inflating the metric by masking genuine miscounts.",
            "",
            "| model | L2 valid strict | L2 valid trunc | Δ | "
            "L3 PACT strict | L3 PACT trunc | Δ |",
            "|---|---|---|---|---|---|---|"]
        for spec, r in results["models"].items():
            l2s = r["L2_generation"]["PACT"]["valid_after_retries"]
            l2t = r["L2_generation_truncate"]["PACT"]["valid_after_retries"]
            l3s = r["L3_two_hop"]["PACT"]["end_task_accuracy"]
            l3t = r["L3_two_hop_truncate"]["PACT"]["end_task_accuracy"]
            lines.append(
                f"| {spec} | {l2s} | {l2t} | {round(l2t - l2s, 3):+} "
                f"| {l3s} | {l3t} | {round(l3t - l3s, 3):+} |")
        lines.append("")

    if ab_derived:
        lines += [
            "## RFC-02 A/B — declared → derived-count (PACT, strict)", "",
            "Does removing the model's self-count gate recover the "
            "over-emission failures? `derived − declared` > 0 means "
            "derived-count legitimately recovers valid payloads the declared "
            "`name[N]` gate rejected — rows are kept and the count is derived "
            "from them (unlike truncate, which drops rows). Every other "
            "fail-closed check is identical in both columns.", "",
            "| model | L2 valid decl | L2 valid deriv | Δ | L3 PACT decl | "
            "L3 PACT deriv | Δ | L3 PACT-dec decl | L3 PACT-dec deriv | Δ |",
            "|---|---|---|---|---|---|---|---|---|---|"]
        for spec, r in results["models"].items():
            l2d = r["L2_generation_declared"]["PACT"]["valid_after_retries"]
            l2v = r["L2_generation"]["PACT"]["valid_after_retries"]
            l3d = r["L3_two_hop_declared"]["PACT"]["end_task_accuracy"]
            l3v = r["L3_two_hop"]["PACT"]["end_task_accuracy"]
            pdd = r["L3_two_hop_declared"]["PACT-decoded"]["end_task_accuracy"]
            pdv = r["L3_two_hop"]["PACT-decoded"]["end_task_accuracy"]
            lines.append(
                f"| {spec} | {l2d} | {l2v} | {round(l2v - l2d, 3):+} "
                f"| {l3d} | {l3v} | {round(l3v - l3d, 3):+} "
                f"| {pdd} | {pdv} | {round(pdv - pdd, 3):+} |")
        lines.append("")
    tc = trailing_count_stats()
    tc = {s: v for s, v in tc.items() if s in results["models"]}
    if tc:
        lines += [
            "## v0.2 post-emission trailing-count accuracy (`#n=`)", "",
            "Of each model's `name[*]` emissions, how often is the trailing "
            "`#n=N` correct (== rows emitted)? This is the RFC-02 bet measured "
            "directly: models counting rows they have already streamed. "
            "`wrong` = a miscount the decoder rejects fail-closed (integrity "
            "preserved); `missing` = no trailer, decodes with "
            "count_verified=false.", "",
            "| model | emissions | correct | wrong | missing | correct % |",
            "|---|---|---|---|---|---|"]
        for spec in results["models"]:
            v = tc.get(spec)
            if not v:
                continue
            n = v["total"] or 1
            lines.append(
                f"| {spec} | {v['total']} | {v['correct']} | {v['wrong']} "
                f"| {v['missing']} | {100*v['correct']/n:.0f}% |")
        lines.append("")

    lines += ["## Token usage (from provider usage fields)", "",
              "| model | input | output | cached input | api calls |", "|---|---|---|---|---|"]
    for spec, r in results["models"].items():
        u = r["token_usage"]
        lines.append(f"| {spec} | {u['in']} | {u['out']} | {u['cached_in']} "
                     f"| {r['api_calls']} |")
    open(os.path.join(RESULTS, "LIVE_RESULTS.md"), "w",
         encoding="utf-8").write("\n".join(lines))
    print("\nwritten: results/live_results.json + results/LIVE_RESULTS.md")


if __name__ == "__main__":
    main()
