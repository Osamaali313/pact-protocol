"""PACT v0.3 — Prompt-cache-Aware Constrained Typed protocol.

Core library: schema model, dual-profile wire codec, grammar synthesis.

Two wire profiles share one schema/sid:
  * machine (codec->codec): leading `name[N]` count, enforced. Unchanged.
  * model (LLM-emitted): `name[*]` with a trailing `#n=<count>` line after the
    last row. Decode enforces the trailing count when present, and flags
    count_verified=False when `name[*]` arrives with no trailer.

v0.3 adds an OPTIONAL inline field echo on the body line —
`name[*]{f1,f2,...}` — a pure rendering of the already-negotiated schema for
locality (fields adjacent to the rows a model reads). It changes NEITHER the
schema canonicalization NOR the sid: the sid hashes the schema, not the message
syntax. The wire/codec PROTOCOL_VERSION bumps to 0.3, but Schema.version (the
schema-contract version that feeds `canonical()` and the sid) stays 0.2 — the
type contract is unchanged. The decoder verifies any echo present against the
schema fail-closed (field_echo_mismatch is a corruption class); the echo is
optional and both forms decode.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

__version__ = "0.3.0"
PROTOCOL_VERSION = "0.3"   # wire/codec version; decoupled from Schema.version
                           # (the schema-contract version in the sid) so an
                           # additive wire feature never moves content addresses.
__all__ = ["Field", "Schema", "encode", "decode", "gbnf_from_schema",
           "row_validators", "render", "PactError"]


class PactError(ValueError):
    """Raised on any protocol violation. PACT is fail-closed by design."""


# ---------------------------------------------------------------- schema ---

@dataclass(frozen=True)
class Field:
    name: str
    ftype: str                       # str | int | float | bool | enum
    enum: tuple[str, ...] | None = None
    lo: float | None = None
    hi: float | None = None


@dataclass(frozen=True)
class Schema:
    name: str
    fields: tuple[Field, ...]
    version: str = "0.2"

    def canonical(self) -> str:
        parts = [f"{self.name}@{self.version}"]
        for f in self.fields:
            s = f"{f.name}:{f.ftype}"
            if f.enum:
                s += "{" + "|".join(f.enum) + "}"
            if f.lo is not None or f.hi is not None:
                s += f"[{f.lo},{f.hi}]"
            parts.append(s)
        return ";".join(parts)

    @property
    def sid(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()[:8]

    def header(self) -> str:
        """Schema block: negotiated once per session, prompt-cache resident."""
        lines = [f"!schema {self.name} sid={self.sid} v={self.version}"]
        for f in self.fields:
            t = f.ftype if not f.enum else "enum{" + ",".join(f.enum) + "}"
            if f.lo is not None or f.hi is not None:
                t += f" range[{f.lo},{f.hi}]"
            lines.append(f"  {f.name}: {t}")
        return "\n".join(lines)


# ------------------------------------------------------------ wire codec ---

_HDR = re.compile(r">(\w+) s=(\S+) r=(\S+) c=(\S+) sid=([0-9a-f]{8})$")
# body line, both profiles, with an OPTIONAL v0.3 field echo {f1,f2,...}:
_BODY_DECL = re.compile(r"(\w+)\[(\d+)\](?:\{([^}]*)\})?$")   # name[N]{echo?}
_BODY_STAR = re.compile(r"(\w+)\[\*\](?:\{([^}]*)\})?$")       # name[*]{echo?}
_TRAILER = re.compile(r"#n=(\d+)$")            # model profile trailing count
_SPLIT = re.compile(r"(?<!\\),")


def _check_field_echo(echo: str | None, schema: Schema) -> None:
    """v0.3: if a field echo is present, it MUST list the schema's field names
    in order. A mismatch is a fail-closed integrity violation (the echo claims
    a different column layout than the sid's schema)."""
    if echo is None:
        return
    got = echo.split(",")
    want = [f.name for f in schema.fields]
    if got != want:
        raise PactError(f"field echo {got} != schema fields {want}")


def _esc(v: Any) -> str:
    if isinstance(v, bool):
        return "T" if v else "F"
    if v is None:
        return "~"
    return (str(v).replace("\\", "\\\\").replace(",", "\\,")
            .replace("\n", "\\n"))


def _unesc(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            c = s[i + 1]
            out.append({"n": "\n", ",": ",", "\\": "\\"}.get(c, c))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _coerce(raw: str, f: Field) -> Any:
    if raw == "~":
        return None
    try:
        if f.ftype == "int":
            return int(raw)
        if f.ftype == "float":
            return float(raw)
    except ValueError as e:
        raise PactError(f"{f.ftype} field {f.name}: bad literal {raw!r}") from e
    if f.ftype == "bool":
        if raw not in ("T", "F"):
            raise PactError(f"bool field {f.name}: bad literal {raw!r}")
        return raw == "T"
    return _unesc(raw)


def encode(records: list[dict], schema: Schema, *, msg_type: str = "tell",
           sender: str = "a", receiver: str = "b", corr: str = "0",
           provenance: dict[str, str] | None = None,
           profile: str = "machine", field_echo: bool = False) -> str:
    """Encode records to a PACT wire message.

    profile="machine" (default, codec->codec): leading `name[N]` count.
    profile="model": the v0.2 model-emitted profile — `name[*]` and a trailing
    `#n=<count>` line after the last row (and after provenance). Both decode
    fail-closed; the model profile is what gbnf_from_schema constrains an LLM
    to produce and what session_prompt instructs.

    field_echo=True (v0.3): append the schema field names inline on the body
    line — `name[N]{f1,f2,...}` / `name[*]{f1,f2,...}`. Pure locality; decode
    verifies it against the schema. sid/canonicalization are unaffected."""
    names = [f.name for f in schema.fields]
    count = "*" if profile == "model" else str(len(records))
    echo = "{" + ",".join(names) + "}" if field_echo else ""
    body = f"{schema.name}[{count}]{echo}"
    lines = [f">{msg_type} s={sender} r={receiver} c={corr} sid={schema.sid}",
             body]
    lines += [",".join(_esc(r.get(n)) for n in names) for r in records]
    if provenance:
        lines += [f"^{k}={v}" for k, v in provenance.items()]
    if profile == "model":
        lines.append(f"#n={len(records)}")
    return "\n".join(lines)


def decode(wire: str, registry: dict[str, Schema]) -> dict:
    """Fail-closed decode of both v0.2 wire profiles.

    Machine `name[N]`: N enforced against the row count.
    Model `name[*]`: a trailing `#n=N` line is enforced when present (restoring
    truncation detection); when absent, the count is derived from the rows and
    the result carries count_verified=False so a caller can reject unverified
    messages. Unknown sid, per-row arity, enum, range, and bool-literal
    violations raise PactError under BOTH profiles — no repair path."""
    lines = wire.strip().split("\n")
    if len(lines) < 2:
        raise PactError("truncated message")
    m = _HDR.match(lines[0])
    if not m:
        raise PactError("malformed header")
    msg_type, sender, receiver, corr, sid = m.groups()
    schema = registry.get(sid)
    if schema is None:
        raise PactError(f"unknown schema sid={sid}; renegotiate")
    md = _BODY_DECL.match(lines[1])
    ms = _BODY_STAR.match(lines[1])
    if md and md.group(1) == schema.name:
        mode, declared, echo = "declared", int(md.group(2)), md.group(3)
    elif ms and ms.group(1) == schema.name:
        mode, declared, echo = "star", None, ms.group(2)
    else:
        raise PactError("schema name mismatch")
    _check_field_echo(echo, schema)   # v0.3: fail-closed if echo != fields
    records, prov, trailing = [], {}, None
    for line in lines[2:]:
        if line.startswith("^"):
            k, _, v = line[1:].partition("=")
            prov[k] = v
            continue
        mt = _TRAILER.match(line)
        if mt:
            if mode != "star":
                raise PactError("trailing #n= only valid with name[*]")
            if trailing is not None:
                raise PactError("duplicate #n= trailer")
            trailing = int(mt.group(1))
            continue
        raw = _SPLIT.split(line)
        if len(raw) != len(schema.fields):
            raise PactError(f"arity {len(raw)} != {len(schema.fields)}")
        rec = {}
        for r, f in zip(raw, schema.fields):
            v = _coerce(r, f)
            if v is not None:
                if f.enum and v not in f.enum:
                    raise PactError(f"{f.name}={v!r} outside enum")
                if f.lo is not None and v < f.lo:
                    raise PactError(f"{f.name}={v} < lo={f.lo}")
                if f.hi is not None and v > f.hi:
                    raise PactError(f"{f.name}={v} > hi={f.hi}")
            rec[f.name] = v
        records.append(rec)
    count_verified = True
    if mode == "declared":
        if len(records) != declared:
            raise PactError(f"declared {declared} rows, got {len(records)}")
    elif trailing is not None:
        if len(records) != trailing:
            raise PactError(f"trailing #n={trailing} != {len(records)} rows")
    else:
        count_verified = False   # name[*] with no trailer: derived, unverified
    return {"type": msg_type, "from": sender, "to": receiver, "corr": corr,
            "schema": schema.name, "records": records, "provenance": prov,
            "count_verified": count_verified}


# ------------------------------------------------- grammar synthesis (L3) ---

def gbnf_from_schema(schema: Schema) -> str:
    """Derive a llama.cpp-compatible GBNF grammar from a schema.

    Loading this grammar into the responder's constrained decoder makes
    replies valid-by-construction: this is the CQL enforcement mechanism
    with a concrete payload language.
    """
    # v0.3: the grammar emits the model profile WITH the field echo, so a
    # constrained decoder produces `name[*]{f1,f2,...}` — locality for free and
    # the echo is verified on decode.
    echo = "{" + ",".join(f.name for f in schema.fields) + "}"
    rules = [
        "root ::= header body trailer",
        f'header ::= ">" msgtype " s=" ident " r=" ident " c=" ident'
        f' " sid=" "{schema.sid}" "\\n" "{schema.name}" "[*]" "{echo}" "\\n"',
        'msgtype ::= "tell" | "ask" | "propose" | "confirm" | "reject"',
        "ident ::= [a-zA-Z0-9_-]+",
        "count ::= [0-9]+",
        'body ::= row ("\\n" row)*',
        'trailer ::= "\\n" "#n=" count',
    ]
    cells = []
    for i, f in enumerate(schema.fields):
        rn = f"f{i}"
        cells.append(rn)
        if f.ftype == "enum" and f.enum:
            rules.append(f"{rn} ::= " + " | ".join(f'"{v}"' for v in f.enum))
        elif f.ftype == "int":
            rules.append(f'{rn} ::= "-"? [0-9]+')
        elif f.ftype == "float":
            rules.append(f'{rn} ::= "-"? [0-9]+ ("." [0-9]+)?')
        elif f.ftype == "bool":
            rules.append(f'{rn} ::= "T" | "F"')
        else:
            rules.append(f'{rn} ::= ( [^,\\n\\\\] | "\\\\" . )*')
    rules.append("row ::= " + ' "," '.join(cells))
    return "\n".join(rules)


def row_validators(schema: Schema) -> list:
    """Per-field regex validators equivalent to the GBNF terminals —
    used by the harness to verify grammar/decoder agreement."""
    vals = []
    for f in schema.fields:
        if f.ftype == "enum" and f.enum:
            vals.append(re.compile("^(" + "|".join(map(re.escape, f.enum))
                                   + ")$"))
        elif f.ftype == "int":
            vals.append(re.compile(r"^-?[0-9]+$"))
        elif f.ftype == "float":
            vals.append(re.compile(r"^-?[0-9]+(\.[0-9]+)?$"))
        elif f.ftype == "bool":
            vals.append(re.compile(r"^[TF]$"))
        else:
            vals.append(re.compile(r"^(?:[^,\n\\]|\\.)*$"))
    return vals


# ---------------------------------------------- consumption-mode rendering ---

def render(records: list[dict], schema: Schema, style: str = "keyed") -> str:
    """Render decoded records for a MODEL reader. This is the *consumption*
    layer, not a wire format: the wire stays codec-dense; a receiving agent
    decodes it (zero-token, in code) and re-renders per policy only when a model
    actually needs to read it.

      style="keyed": one `f=v f=v ...` line per record — labels adjacent to
        values (highest comprehension; the mode PACT-decoded used in L3).
      style="table": a field-header line then TOON-shaped comma rows — denser
        than keyed, labels once at the top.

    A code consumer never calls this (it works on the dict records directly);
    a model-raw consumer reads the wire itself and relies on the field echo."""
    names = [f.name for f in schema.fields]
    if style == "keyed":
        return "\n".join(
            " ".join(f"{n}={r.get(n)}" for n in names) for r in records)
    if style == "table":
        head = ",".join(names)
        rows = [",".join(_esc(r.get(n)) for n in names) for r in records]
        return "\n".join([head] + rows)
    raise ValueError(f"unknown render style: {style!r} (keyed|table)")
