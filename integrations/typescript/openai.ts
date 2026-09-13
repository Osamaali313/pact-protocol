/** PACT over any OpenAI-compatible endpoint: OpenAI, OpenRouter, Groq,
 *  DeepSeek, MiniMax, xAI — switch with PACT_BASE_URL / PACT_MODEL.
 *  npm i openai pact-wire */
import OpenAI from "openai";
import { askPact } from "pact-wire";
import { WALLS, sessionPrompt } from "./shared.js";

const client = new OpenAI({ baseURL: process.env.PACT_BASE_URL });
const MODEL = process.env.PACT_MODEL ?? "gpt-4.1-mini";
const SYSTEM = sessionPrompt(WALLS);  // byte-identical every call → prefix cache

const complete = async (prompt: string): Promise<string> => {
  const resp = await client.chat.completions.create({
    model: MODEL,
    messages: [{ role: "system", content: SYSTEM },
               { role: "user", content: prompt }],
  });
  return resp.choices[0]?.message?.content ?? "";
};

const msg = await askPact(complete,
  `>ask s=user r=extractor c=1 sid=${WALLS.sid}\nEmit 3 example walls on L01.`,
  WALLS);
console.log(msg.records);
