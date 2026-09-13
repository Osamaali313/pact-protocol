/** PACT TS SDK tests — run with: npm test (tsc + node --test). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Schema, Field, encode, decode, gbnfFromSchema, render, askPact, PactError } from "../src/index.js";

const walls = new Schema("walls", [
  { name: "id", ftype: "str" },
  { name: "level", ftype: "enum", enum: ["L01", "L02", "L03", "L04", "ROOF"] },
  { name: "fire_rating", ftype: "enum", enum: ["EI30", "EI60", "EI90", "EI120", "NR"] },
  { name: "thickness_mm", ftype: "int", lo: 50, hi: 600 },
  { name: "load_bearing", ftype: "bool" },
  { name: "confidence", ftype: "float", lo: 0.0, hi: 1.0 },
] as Field[]);

test("sid parity with the Python reference implementation", () => {
  // The Python library computes 226909cf for this exact schema under v0.2
  // (walls@0.2; the wire-syntax bump changed the sid from v0.1's bf20a9b4).
  assert.equal(walls.sid, "226909cf");
});

test("lossless round-trip incl. escaping edge cases", () => {
  const recs = [
    { id: "W-100", level: "L01", fire_rating: "EI30", thickness_mm: 150, load_bearing: true, confidence: 0.62 },
    { id: "a,b\nc\\d", level: "ROOF", fire_rating: "NR", thickness_mm: 600, load_bearing: false, confidence: 1.0 },
    { id: "", level: "L02", fire_rating: "EI120", thickness_mm: 50, load_bearing: true, confidence: null },
  ];
  const wire = encode(recs, walls, { sender: "extractor", receiver: "planner", corr: "7f3a" });
  const out = decode(wire, { [walls.sid]: walls });
  assert.deepEqual(out.records, recs);
});

test("fail-closed: all six corruption classes rejected", () => {
  const recs = [{ id: "W-1", level: "L01", fire_rating: "EI30", thickness_mm: 150, load_bearing: true, confidence: 0.5 }];
  const wire = encode(recs, walls);
  const reg = { [walls.sid]: walls };
  const corrupt = [
    wire.replace("L01", "L99"),          // enum drift
    wire.replace("150", "9999"),         // range violation
    wire.replace(",T,", ","),            // arity drop (also bool slot)
    wire.replace("150", "thick"),        // type swap
    wire.replace(",T,", ",yes,"),        // bool literal
    wire.replace(walls.sid, "deadbeef"), // sid spoof
  ];
  for (const bad of corrupt) assert.throws(() => decode(bad, reg), PactError);
});

test("row-count mismatch rejected", () => {
  const recs = [
    { id: "W-1", level: "L01", fire_rating: "EI30", thickness_mm: 150, load_bearing: true, confidence: 0.5 },
    { id: "W-2", level: "L02", fire_rating: "EI60", thickness_mm: 200, load_bearing: false, confidence: 0.9 },
  ];
  const wire = encode(recs, walls).replace("walls[2]", "walls[3]");
  assert.throws(() => decode(wire, { [walls.sid]: walls }), PactError);
});

test("provenance survives the round trip", () => {
  const recs = [{ id: "W-1", level: "L01", fire_rating: "EI30", thickness_mm: 150, load_bearing: true, confidence: 0.5 }];
  const wire = encode(recs, walls, { provenance: { "W-1": "ifc:2O2Fr$t4X@HolterTower.ifc" } });
  const out = decode(wire, { [walls.sid]: walls });
  assert.equal(out.provenance["W-1"], "ifc:2O2Fr$t4X@HolterTower.ifc");
});

test("grammar synthesis embeds sid, model profile, and enum terminals", () => {
  const g = gbnfFromSchema(walls);
  assert.ok(g.includes('"226909cf"'));
  assert.ok(g.includes('"[*]"'));                 // v0.2 model profile
  assert.ok(g.includes('trailer ::= "\\n" "#n=" count'));
  assert.ok(g.includes('"EI120"'));
  assert.ok(g.includes('f4 ::= "T" | "F"'));
});

test("v0.3 field echo: name[*]{fields} round-trips, is optional, verified fail-closed, sid unchanged", () => {
  const recs = [{ id: "W-1", level: "L02", fire_rating: "EI60", thickness_mm: 200, load_bearing: true, confidence: 0.9 }];
  const reg = { [walls.sid]: walls };
  // sid must NOT move (echo is pure message syntax) — reverse tripwire
  assert.equal(walls.sid, "226909cf");
  const wire = encode(recs, walls, { profile: "model", fieldEcho: true });
  assert.ok(wire.includes("walls[*]{id,level,fire_rating,thickness_mm,load_bearing,confidence}"));
  assert.deepEqual(decode(wire, reg).records, recs);
  // optional: no-echo model wire still decodes
  assert.deepEqual(decode(encode(recs, walls, { profile: "model" }), reg).records, recs);
  // mismatch (rename) rejected fail-closed
  assert.throws(() => decode(wire.replace("fire_rating", "fire_rate"), reg), PactError);
  // gbnf embeds the echo
  assert.ok(gbnfFromSchema(walls).includes("{id,level,fire_rating,thickness_mm,load_bearing,confidence}"));
});

test("v0.2 model profile: name[*] + trailing #n= round-trips and enforces count", () => {
  const recs = [
    { id: "W-1", level: "L02", fire_rating: "EI60", thickness_mm: 200, load_bearing: true, confidence: 0.9 },
    { id: "W-2", level: "ROOF", fire_rating: "NR", thickness_mm: 120, load_bearing: false, confidence: 0.7 },
  ];
  const reg = { [walls.sid]: walls };
  const wire = encode(recs, walls, { profile: "model", sender: "extractor", receiver: "planner", corr: "q" });
  assert.ok(wire.includes("walls[*]"));
  assert.ok(wire.trimEnd().endsWith("#n=2"));
  const out = decode(wire, reg);
  assert.deepEqual(out.records, recs);
  assert.equal(out.countVerified, true);
  // row_truncation: drop the last data row, keep the #n=2 trailer -> rejected
  const lines = wire.split("\n");
  lines.splice(lines.length - 2, 1);            // remove last value row, keep #n=2
  assert.throws(() => decode(lines.join("\n"), reg), PactError);
  // name[*] with no trailer -> derived, countVerified=false (not thrown)
  const noTrailer = wire.split("\n").slice(0, -1).join("\n");
  const d2 = decode(noTrailer, reg);
  assert.equal(d2.countVerified, false);
  assert.equal(d2.records.length, 2);
});

test("v0.3 render(): keyed and table consumption views", () => {
  const recs = [
    { id: "W-1", level: "L02", fire_rating: "EI60", thickness_mm: 200, load_bearing: true, confidence: 0.9 },
    { id: "W-2", level: "ROOF", fire_rating: "NR", thickness_mm: 120, load_bearing: false, confidence: 0.7 },
  ];
  const keyed = render(recs, walls, "keyed");
  assert.equal(keyed.split("\n").length, 2);
  assert.ok(keyed.startsWith("id=W-1 level=L02 fire_rating=EI60"));
  const table = render(recs, walls, "table");
  assert.equal(table.split("\n")[0], "id,level,fire_rating,thickness_mm,load_bearing,confidence");
  assert.equal(table.split("\n")[1], "W-1,L02,EI60,200,T,0.9");
});

test("askPact: validate-and-retry loop recovers from a corrupt first reply", async () => {
  const good = encode(
    [{ id: "W-9", level: "L03", fire_rating: "EI90", thickness_mm: 300, load_bearing: false, confidence: 0.8 }],
    walls, { sender: "extractor", receiver: "planner", corr: "x1" });
  let calls = 0;
  const mockLLM = async (prompt: string) => {
    calls++;
    if (calls === 1) return "Sure! Here you go:\n" + good.replace("EI90", "EI999"); // corrupt
    assert.ok(prompt.includes("violated the schema"));                              // feedback loop engaged
    return "Here is the corrected message:\n" + good;
  };
  const out = await askPact(mockLLM, "list walls on L03", walls);
  assert.equal(calls, 2);
  assert.equal(out.records[0].fire_rating, "EI90");
});

test("askPact: fail-closed after exhausting retries", async () => {
  const alwaysBad = async () => ">tell s=a r=b c=0 sid=deadbeef\nwalls[0]";
  await assert.rejects(askPact(alwaysBad, "q", walls, { maxRetries: 1 }), PactError);
});
