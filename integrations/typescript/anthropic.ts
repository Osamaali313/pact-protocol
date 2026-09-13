/** PACT over @anthropic-ai/sdk — schema in a cache_control'd system block.
 *  npm i @anthropic-ai/sdk pact-wire   ·   export ANTHROPIC_API_KEY=... */
import Anthropic from "@anthropic-ai/sdk";
import { Schema, askPact } from "pact-wire";
import { WALLS, sessionPrompt } from "./shared.js";

const client = new Anthropic();

const complete = async (prompt: string): Promise<string> => {
  const resp = await client.messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 1024,
    system: [{
      type: "text",
      text: sessionPrompt(WALLS, "You are the extractor agent."),
      cache_control: { type: "ephemeral" },   // L1 rides the prompt cache
    }],
    messages: [{ role: "user", content: prompt }],
  });
  return resp.content.filter(b => b.type === "text").map(b => (b as any).text).join("");
};

const msg = await askPact(complete,
  `>ask s=planner r=extractor c=7f3a sid=${WALLS.sid}\nList 3 plausible fire-rated walls on L02.`,
  WALLS);
console.log(msg.records);   // schema-valid or PactError thrown
