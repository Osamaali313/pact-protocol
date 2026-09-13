/** Shared schema + cache-resident session prompt for the TS examples. */
import { Schema, type Field } from "pact-wire";

export const WALLS = new Schema("walls", [
  { name: "id", ftype: "str" },
  { name: "level", ftype: "enum", enum: ["L01", "L02", "L03", "L04", "ROOF"] },
  { name: "fire_rating", ftype: "enum", enum: ["EI30", "EI60", "EI90", "EI120", "NR"] },
  { name: "thickness_mm", ftype: "int", lo: 50, hi: 600 },
  { name: "load_bearing", ftype: "bool" },
  { name: "confidence", ftype: "float", lo: 0, hi: 1 },
] as Field[]);

/** Static, cache-resident block: put FIRST in the system prompt,
 *  byte-identical on every call (Anthropic cache_control / OpenAI prefix cache). */
export function sessionPrompt(schema: Schema, roleHint = ""): string {
  return [
    "You exchange data using the PACT wire protocol.",
    "Reply with ONLY a PACT message — no prose, no markdown fences.",
    "Message shape:",
    ">tell s=<you> r=<recipient> c=<corr> sid=<schema sid>",
    "<name>[<row count>]",
    "<value rows, comma-separated, schema field order>",
    "Booleans are T/F; null is ~; escape commas as \\, and newlines as \\n.",
    "",
    `Negotiated schema (sid=${schema.sid}):`,
    schema.header(),
    roleHint,
  ].join("\n");
}
