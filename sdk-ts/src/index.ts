/**
 * PACT v0.3 — TypeScript reference implementation.
 * Mirrors src/pact/__init__.py exactly: same canonicalization → same sid,
 * same dual-profile wire syntax, same fail-closed semantics.
 *
 * Dual profile (shared schema/sid):
 *   - machine (codec→codec): leading `name[N]`, enforced. Unchanged.
 *   - model (LLM-emitted): `name[*]` + a trailing `#n=<count>` line after the
 *     last row. decode enforces the trailing count when present; a `name[*]`
 *     with no trailer decodes with countVerified=false.
 *
 * v0.3 adds an OPTIONAL inline field echo on the body line —
 * `name[*]{f1,f2,...}` — a pure rendering of the negotiated schema (locality).
 * It changes NEITHER canonicalization NOR the sid; PROTOCOL_VERSION bumps to
 * 0.3 while Schema.version (the sid input) stays 0.2. decode verifies any echo
 * against the schema fail-closed; the echo is optional (both forms decode).
 *
 * Zero runtime dependencies (node:crypto only). Works in Node ≥ 16;
 * for browsers/edge, swap sha256 for WebCrypto (async) — see README.
 */

import { createHash } from "node:crypto";

export const PROTOCOL_VERSION = "0.3"; // wire/codec version; decoupled from sid

export type FType = "str" | "int" | "float" | "bool" | "enum";

export interface Field {
  name: string;
  ftype: FType;
  enum?: readonly string[];
  lo?: number;
  hi?: number;
}

export type Value = string | number | boolean | null;
export type Rec = Record<string, Value>;

export class PactError extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = "PactError";
  }
}

/** Match Python's repr of a bound for canonicalization parity:
 *  int-typed fields declare int bounds (50 → "50"); float-typed fields
 *  declare float bounds (0 → "0.0", 0.5 → "0.5"). */
function boundRepr(x: number, ftype: FType): string {
  if (ftype === "float" && Number.isInteger(x)) return `${x}.0`;
  return String(x);
}

export class Schema {
  readonly name: string;
  readonly fields: readonly Field[];
  readonly version: string;

  constructor(name: string, fields: readonly Field[], version = "0.2") {
    this.name = name;
    this.fields = fields;
    this.version = version;
  }

  canonical(): string {
    const parts = [`${this.name}@${this.version}`];
    for (const f of this.fields) {
      let s = `${f.name}:${f.ftype}`;
      if (f.enum) s += "{" + f.enum.join("|") + "}";
      if (f.lo !== undefined || f.hi !== undefined) {
        const lo = f.lo === undefined ? "None" : boundRepr(f.lo, f.ftype);
        const hi = f.hi === undefined ? "None" : boundRepr(f.hi, f.ftype);
        s += `[${lo},${hi}]`;
      }
      parts.push(s);
    }
    return parts.join(";");
  }

  get sid(): string {
    return createHash("sha256").update(this.canonical()).digest("hex").slice(0, 8);
  }

  /** Schema block: negotiated once per session, prompt-cache resident. */
  header(): string {
    const lines = [`!schema ${this.name} sid=${this.sid} v=${this.version}`];
    for (const f of this.fields) {
      let t: string = f.ftype;
      if (f.enum) t = "enum{" + f.enum.join(",") + "}";
      if (f.lo !== undefined || f.hi !== undefined) t += ` range[${f.lo},${f.hi}]`;
      lines.push(`  ${f.name}: ${t}`);
    }
    return lines.join("\n");
  }
}

// ------------------------------------------------------------- wire codec ---

const HDR = /^>(\w+) s=(\S+) r=(\S+) c=(\S+) sid=([0-9a-f]{8})$/;
// body line, both profiles, with an OPTIONAL v0.3 field echo {f1,f2,...}:
const BODY_DECL = /^(\w+)\[(\d+)\](?:\{([^}]*)\})?$/;   // name[N]{echo?}
const BODY_STAR = /^(\w+)\[\*\](?:\{([^}]*)\})?$/;       // name[*]{echo?}
const TRAILER = /^#n=(\d+)$/;            // model profile trailing count

/** v0.3: if a field echo is present it MUST list the schema's field names in
 *  order — a mismatch is a fail-closed integrity violation. */
function checkFieldEcho(echo: string | undefined, schema: Schema): void {
  if (echo === undefined) return;
  const got = echo.split(",");
  const want = schema.fields.map((f) => f.name);
  if (got.length !== want.length || got.some((g, i) => g !== want[i]))
    throw new PactError(`field echo [${got}] != schema fields [${want}]`);
}

function esc(v: Value): string {
  if (v === true) return "T";
  if (v === false) return "F";
  if (v === null || v === undefined) return "~";
  return String(v).replace(/\\/g, "\\\\").replace(/,/g, "\\,").replace(/\n/g, "\\n");
}

function unesc(s: string): string {
  let out = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && i + 1 < s.length) {
      const c = s[i + 1];
      out += c === "n" ? "\n" : c;
      i++;
    } else out += s[i];
  }
  return out;
}

function splitUnescaped(line: string): string[] {
  const cells: string[] = [""];
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === "\\" && i + 1 < line.length) {
      cells[cells.length - 1] += c + line[i + 1];
      i++;
    } else if (c === ",") {
      cells.push("");
    } else cells[cells.length - 1] += c;
  }
  return cells;
}

function coerce(raw: string, f: Field): Value {
  if (raw === "~") return null;
  switch (f.ftype) {
    case "int": {
      if (!/^-?[0-9]+$/.test(raw)) throw new PactError(`int field ${f.name}: bad literal ${JSON.stringify(raw)}`);
      return parseInt(raw, 10);
    }
    case "float": {
      if (!/^-?[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?$/.test(raw)) throw new PactError(`float field ${f.name}: bad literal ${JSON.stringify(raw)}`);
      return parseFloat(raw);
    }
    case "bool": {
      if (raw !== "T" && raw !== "F") throw new PactError(`bool field ${f.name}: bad literal ${JSON.stringify(raw)}`);
      return raw === "T";
    }
    default:
      return unesc(raw);
  }
}

export interface EncodeOpts {
  msgType?: "tell" | "ask" | "propose" | "confirm" | "reject";
  sender?: string;
  receiver?: string;
  corr?: string;
  provenance?: Record<string, string>;
  /** "machine" (default, codec→codec): leading name[N]. "model": name[*] with
   *  a trailing #n=<count> line — the v0.2 LLM-emitted profile. */
  profile?: "machine" | "model";
  /** v0.3: append the schema field names inline — name[N]{f1,f2,...}. */
  fieldEcho?: boolean;
}

export function encode(records: readonly Rec[], schema: Schema, opts: EncodeOpts = {}): string {
  const { msgType = "tell", sender = "a", receiver = "b", corr = "0", provenance, profile = "machine", fieldEcho = false } = opts;
  const names = schema.fields.map((f) => f.name);
  const count = profile === "model" ? "*" : String(records.length);
  const echo = fieldEcho ? `{${names.join(",")}}` : "";
  const lines = [
    `>${msgType} s=${sender} r=${receiver} c=${corr} sid=${schema.sid}`,
    `${schema.name}[${count}]${echo}`,
    ...records.map((r) => names.map((n) => esc(r[n] ?? null)).join(",")),
  ];
  if (provenance) for (const [k, v] of Object.entries(provenance)) lines.push(`^${k}=${v}`);
  if (profile === "model") lines.push(`#n=${records.length}`);
  return lines.join("\n");
}

export interface Decoded {
  type: string;
  from: string;
  to: string;
  corr: string;
  schema: string;
  records: Rec[];
  provenance: Record<string, string>;
  /** false only for a model-profile `name[*]` with no trailing #n= line: the
   *  count was derived from the rows, not verified. Callers may reject on it. */
  countVerified: boolean;
}

/** Fail-closed decode of both v0.2 profiles: any violation throws PactError.
 *  No repair path. */
export function decode(wire: string, registry: Record<string, Schema>): Decoded {
  const lines = wire.trim().split("\n");
  if (lines.length < 2) throw new PactError("truncated message");
  const m = HDR.exec(lines[0]);
  if (!m) throw new PactError("malformed header");
  const [, msgType, sender, receiver, corr, sid] = m;
  const schema = registry[sid];
  if (!schema) throw new PactError(`unknown schema sid=${sid}; renegotiate`);
  const md = BODY_DECL.exec(lines[1]);
  const ms = BODY_STAR.exec(lines[1]);
  let mode: "declared" | "star";
  let declared = 0;
  if (md && md[1] === schema.name) { mode = "declared"; declared = parseInt(md[2], 10); checkFieldEcho(md[3], schema); }
  else if (ms && ms[1] === schema.name) { mode = "star"; checkFieldEcho(ms[2], schema); }
  else throw new PactError("schema name mismatch");

  const records: Rec[] = [];
  const provenance: Record<string, string> = {};
  let trailing: number | null = null;
  for (const line of lines.slice(2)) {
    if (line.startsWith("^")) {
      const i = line.indexOf("=");
      provenance[line.slice(1, i)] = line.slice(i + 1);
      continue;
    }
    const mt = TRAILER.exec(line);
    if (mt) {
      if (mode !== "star") throw new PactError("trailing #n= only valid with name[*]");
      if (trailing !== null) throw new PactError("duplicate #n= trailer");
      trailing = parseInt(mt[1], 10);
      continue;
    }
    const raw = splitUnescaped(line);
    if (raw.length !== schema.fields.length)
      throw new PactError(`arity ${raw.length} != ${schema.fields.length}`);
    const rec: Rec = {};
    raw.forEach((cell, i) => {
      const f = schema.fields[i];
      const v = coerce(cell, f);
      if (v !== null) {
        if (f.enum && !f.enum.includes(v as string))
          throw new PactError(`${f.name}=${JSON.stringify(v)} outside enum`);
        if (f.lo !== undefined && (v as number) < f.lo)
          throw new PactError(`${f.name}=${v} < lo=${f.lo}`);
        if (f.hi !== undefined && (v as number) > f.hi)
          throw new PactError(`${f.name}=${v} > hi=${f.hi}`);
      }
      rec[f.name] = v;
    });
    records.push(rec);
  }
  let countVerified = true;
  if (mode === "declared") {
    if (records.length !== declared)
      throw new PactError(`declared ${declared} rows, got ${records.length}`);
  } else if (trailing !== null) {
    if (records.length !== trailing)
      throw new PactError(`trailing #n=${trailing} != ${records.length} rows`);
  } else {
    countVerified = false;
  }
  return { type: msgType, from: sender, to: receiver, corr, schema: schema.name, records, provenance, countVerified };
}

// ---------------------------------------------- grammar synthesis (L3) -----

/** GBNF grammar for llama.cpp / Outlines-style constrained decoders. */
export function gbnfFromSchema(schema: Schema): string {
  // v0.3: emit the model profile WITH the field echo, so a constrained decoder
  // produces `name[*]{f1,f2,...}` and the echo is verified on decode.
  const echo = `{${schema.fields.map((f) => f.name).join(",")}}`;
  const rules = [
    "root ::= header body trailer",
    `header ::= ">" msgtype " s=" ident " r=" ident " c=" ident " sid=" "${schema.sid}" "\\n" "${schema.name}" "[*]" "${echo}" "\\n"`,
    'msgtype ::= "tell" | "ask" | "propose" | "confirm" | "reject"',
    "ident ::= [a-zA-Z0-9_-]+",
    "count ::= [0-9]+",
    'body ::= row ("\\n" row)*',
    'trailer ::= "\\n" "#n=" count',
  ];
  const cells: string[] = [];
  schema.fields.forEach((f, i) => {
    const rn = `f${i}`;
    cells.push(rn);
    if (f.ftype === "enum" && f.enum) rules.push(`${rn} ::= ` + f.enum.map((v) => `"${v}"`).join(" | "));
    else if (f.ftype === "int") rules.push(`${rn} ::= "-"? [0-9]+`);
    else if (f.ftype === "float") rules.push(`${rn} ::= "-"? [0-9]+ ("." [0-9]+)?`);
    else if (f.ftype === "bool") rules.push(`${rn} ::= "T" | "F"`);
    else rules.push(`${rn} ::= ( [^,\\n\\\\] | "\\\\" . )*`);
  });
  rules.push("row ::= " + cells.join(' "," '));
  return rules.join("\n");
}

// ---------------------------------------------- consumption-mode rendering -

/**
 * Render decoded records for a MODEL reader — the consumption layer, not a
 * wire format. The wire stays codec-dense; a receiving agent decodes it
 * (zero-token, in code) and re-renders per policy only when a model must read
 * it. "keyed": one `f=v f=v ...` line per record (labels adjacent to values;
 * the mode PACT-decoded used). "table": a field-header line then TOON-shaped
 * comma rows.
 */
export function render(records: readonly Rec[], schema: Schema, style: "keyed" | "table" = "keyed"): string {
  const names = schema.fields.map((f) => f.name);
  if (style === "keyed")
    return records.map((r) => names.map((n) => `${n}=${r[n] ?? null}`).join(" ")).join("\n");
  if (style === "table")
    return [names.join(","), ...records.map((r) => names.map((n) => esc(r[n] ?? null)).join(","))].join("\n");
  throw new PactError(`unknown render style: ${style} (keyed|table)`);
}

// ------------------------------------------- validate-and-retry helper -----

export interface AskPactOpts {
  maxRetries?: number;
}

/**
 * Provider-agnostic enforcement loop for API models without decoder hooks
 * (Anthropic, OpenAI, OpenRouter, Groq, DeepSeek, MiniMax, ...).
 * `complete` is any function that takes a prompt and returns model text —
 * wrap your SDK call in it. Fail-closed: throws after maxRetries.
 *
 * For open models served via llama.cpp / vLLM, skip this and pass
 * gbnfFromSchema(schema) to the decoder instead: replies become
 * valid-by-construction and this loop never retries.
 */
export async function askPact(
  complete: (prompt: string) => Promise<string>,
  prompt: string,
  schema: Schema,
  opts: AskPactOpts = {},
): Promise<Decoded> {
  const { maxRetries = 2 } = opts;
  const registry = { [schema.sid]: schema };
  let lastErr = "";
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const suffix = attempt === 0 ? "" :
      `\n\nYour previous reply violated the schema (${lastErr}). ` +
      `Reply again with ONLY a valid PACT message for sid=${schema.sid}.`;
    const text = await complete(prompt + suffix);
    const match = text.match(/>(?:tell|ask|propose|confirm|reject)[\s\S]*/);
    try {
      return decode(match ? match[0] : text, registry);
    } catch (e) {
      lastErr = e instanceof Error ? e.message : String(e);
    }
  }
  throw new PactError(`no valid reply after ${maxRetries + 1} attempts: ${lastErr}`);
}

/** Static, cache-resident session block: put FIRST in the system prompt,
 *  byte-identical on every call (Anthropic cache_control breakpoint /
 *  OpenAI-compatible automatic prefix caching). Mirrors pact.client.session_prompt. */
export function sessionPrompt(schema: Schema, roleHint = "", fieldEcho = false): string {
  const echo = fieldEcho ? `{${schema.fields.map((f) => f.name).join(",")}}` : "";
  const echoLine = fieldEcho
    ? "After [*] add the field echo " + echo + " (the schema field names in " +
      "order) so the columns sit next to the rows, then the value rows.\n"
    : "";
  return (
    "You exchange data using the PACT wire protocol (v0.3 model profile).\n" +
    "Reply with ONLY a PACT message — no prose, no markdown fences.\n" +
    "Message shape:\n" +
    ">tell s=<you> r=<recipient> c=<corr> sid=<schema sid>\n" +
    `<name>[*]${echo}\n` +
    "<value rows, comma-separated, schema field order>\n" +
    "#n=<number of rows you emitted>\n" +
    echoLine +
    "Use the literal marker [*], then write the value rows, then close with a\n" +
    "trailing #n= line stating how many rows you wrote — count them after\n" +
    "writing, do not guess ahead. Booleans are T/F; null is ~; escape commas as\n" +
    "\\, and newlines as \\n.\n\n" +
    `Negotiated schema (sid=${schema.sid}):\n${schema.header()}\n` +
    (roleHint ? `\n${roleHint}` : "")
  );
}
