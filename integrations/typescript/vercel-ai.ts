/** PACT over the Vercel AI SDK — provider-agnostic generateText.
 *  npm i ai @ai-sdk/anthropic pact-wire   (or @ai-sdk/openai, etc.) */
import { generateText } from "ai";
import { anthropic } from "@ai-sdk/anthropic";
import { askPact } from "pact-wire";
import { WALLS, sessionPrompt } from "./shared.js";

const complete = async (prompt: string): Promise<string> => {
  const { text } = await generateText({
    model: anthropic("claude-sonnet-4-6"),
    system: sessionPrompt(WALLS, "You are the extractor agent."),
    prompt,
  });
  return text;
};

const msg = await askPact(complete,
  `>ask s=planner r=extractor c=2 sid=${WALLS.sid}\nList 3 walls on ROOF.`,
  WALLS);
console.log(msg.records);
