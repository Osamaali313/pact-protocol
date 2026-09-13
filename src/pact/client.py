"""Provider-agnostic enforcement loop for API models without decoder hooks.

Works with ANY SDK — Anthropic, OpenAI, OpenRouter, Groq, DeepSeek, MiniMax,
xAI, Vercel AI Gateway — because it only needs a `complete(prompt) -> str`
callable. Wrap your SDK call in that; see integrations/ for ready examples.

For open models served via llama.cpp / vLLM / Outlines, skip this loop and
install `gbnf_from_schema(schema)` in the decoder instead: replies become
valid-by-construction and no retry is ever needed.
"""

from __future__ import annotations

import re
from typing import Callable

from . import Schema, decode, PactError

_MSG = re.compile(r">(?:tell|ask|propose|confirm|reject)[\s\S]*")
_FENCE = re.compile(r"```[a-zA-Z]*\n?")
_BODYLN = re.compile(r"(\w+)\[(\d+)\]$")


def extract_wire(text: str, truncate: bool = False) -> str:
    """Pull a PACT message out of model text.

    Default (truncate=False, fail-closed-preserving): strip markdown fences,
    locate the header, and keep the message body up to the first blank line —
    dropping the closing fence or trailing prose models append after a blank
    separator. It NEVER drops value rows to satisfy the declared count, so a
    genuine row-count mismatch (e.g. the model emits 6 rows under `walls[5]`)
    survives into the strict `decode` and raises PactError. This keeps the
    end-to-end pipeline fail-closed, matching PACT's headline claim.

    Legacy (truncate=True): additionally cap the body at the declared row
    count, silently discarding any over-emitted rows. This raises apparent
    validity but MASKS model miscounts — use only to measure that gap (the
    benchmark reports the delta), never in a production decode path. See
    docs/RFC-02-derived-row-count.md for the durable fix (derive the count
    from the rows instead of trusting the model's self-count)."""
    text = _FENCE.sub("", text)
    m = _MSG.search(text)
    if not m:
        return text.strip()
    lines = m.group(0).split("\n")
    if len(lines) < 2:
        return m.group(0).strip()
    b = _BODYLN.match(lines[1].strip())
    if not b:
        return m.group(0).strip()
    n = int(b.group(2))
    out, taken = [lines[0], lines[1]], 0
    for ln in lines[2:]:
        s = ln.strip()
        if s == "":
            break                       # blank line ends the message body
        if s.startswith("^"):
            out.append(ln)
            continue
        if truncate and taken >= n:
            break                       # legacy: cap at declared count
        out.append(ln)
        taken += 1
    return "\n".join(out)


def ask_pact(complete: Callable[[str], str], prompt: str, schema: Schema,
             *, max_retries: int = 2) -> dict:
    """Send `prompt`, decode the reply fail-closed, retry with error feedback.

    Raises PactError after max_retries + 1 failed attempts (fail-closed:
    the caller never receives an unvalidated record).
    """
    registry = {schema.sid: schema}
    last_err = ""
    for attempt in range(max_retries + 1):
        suffix = "" if attempt == 0 else (
            f"\n\nYour previous reply violated the schema ({last_err}). "
            f"Reply again with ONLY a valid PACT message for sid={schema.sid}.")
        text = complete(prompt + suffix)
        try:
            return decode(extract_wire(text), registry)
        except PactError as e:
            last_err = str(e)
    raise PactError(
        f"no valid reply after {max_retries + 1} attempts: {last_err}")


def session_prompt(schema: Schema, role_hint: str = "",
                   example_rows: list | None = None,
                   field_echo: bool = False) -> str:
    """The static, cache-resident part of the conversation.

    Place this at the very START of the system prompt so provider prompt
    caches (Anthropic cache_control, OpenAI automatic prefix caching) serve
    it at cached-token pricing from the second request onward.

    field_echo=True (v0.3): instruct the model to emit the inline field echo
    `name[*]{f1,f2,...}` so the column names sit next to the rows (locality).
    """
    echo = "{" + ",".join(f.name for f in schema.fields) + "}" if field_echo else ""
    echo_line = (f"After [*] add the field echo {echo} (the schema field names "
                 "in order), then the value rows.\n" if field_echo else "")
    return (
        "You exchange data using the PACT wire protocol (v0.3 model profile).\n"
        "Reply with ONLY a PACT message — no prose, no markdown fences.\n"
        "Message shape:\n"
        ">tell s=<you> r=<recipient> c=<corr> sid=<schema sid>\n"
        f"<name>[*]{echo}\n"
        "<value rows, comma-separated, schema field order>\n"
        "#n=<number of rows you emitted>\n"
        + echo_line +
        "Write the literal marker [*], then the value rows, then close with a\n"
        "trailing #n= line stating how many rows you wrote — count them AFTER\n"
        "writing them, do not guess a count ahead of time. Booleans are T/F; "
        "null is ~; escape commas as \\, and newlines as \\n.\n\n"
        f"Negotiated schema (sid={schema.sid}):\n{schema.header()}\n"
        + (_example_block(schema, example_rows, field_echo) if example_rows else "")
        + (f"\n{role_hint}" if role_hint else "")
    )


def _example_block(schema: Schema, rows: list, field_echo: bool = False) -> str:
    """One worked example in the v0.3 model profile, cache-resident like the
    schema. Shows the field order once and that the trailing #n= equals the
    number of rows emitted."""
    from . import encode
    wire = encode(rows, schema, sender="a", receiver="b", corr="ex",
                  profile="model", field_echo=field_echo)
    fields = ", ".join(f.name for f in schema.fields)
    return (f"\nWorked example (field order: {fields}; the trailing #n="
            f"{len(rows)} equals the row count):\n{wire}\n"
            "Read rows positionally: value k in a row is field k above.\n")
