"""Exact tokenization + baseline serializers for the PACT benchmark.

Tokenizer priority: tiktoken (o200k) if importable and its files are cached,
else a full GPT-2 byte-level BPE (exact, vendored vocab), never a heuristic.
"""

from __future__ import annotations

import json
import os

try:
    import regex as _re
except ImportError:  # pragma: no cover
    raise SystemExit("pip install regex")

_ASSETS = os.path.normpath(os.path.join(os.path.dirname(__file__),
                                        "..", "..", "experiments", "assets"))

_PAT = _re.compile(
    r"""'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+"""
)


def _bytes_to_unicode():
    bs = (list(range(ord("!"), ord("~") + 1))
          + list(range(ord("\xa1"), ord("\xac") + 1))
          + list(range(ord("\xae"), ord("\xff") + 1)))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


class GPT2BPE:
    """Reference byte-level BPE (Radford et al., 2019). Exact token counts."""

    name = "gpt2-bpe (r50k, exact)"

    def __init__(self, assets_dir: str = _ASSETS):
        with open(os.path.join(assets_dir, "encoder.json")) as f:
            self.encoder = json.load(f)
        with open(os.path.join(assets_dir, "vocab.bpe"), encoding="utf-8") as f:
            merges = f.read().split("\n")[1:-1]
        self.bpe_ranks = {tuple(m.split()): i for i, m in enumerate(merges)}
        self.byte_enc = _bytes_to_unicode()
        self.cache: dict[str, str] = {}

    def _bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token)
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            bigram = min(pairs, key=lambda p: self.bpe_ranks.get(p, 1 << 30))
            if bigram not in self.bpe_ranks:
                break
            a, b = bigram
            new, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == a and word[i + 1] == b:
                    new.append(a + b)
                    i += 2
                else:
                    new.append(word[i])
                    i += 1
            word = tuple(new)
        out = " ".join(word)
        self.cache[token] = out
        return out

    def count(self, text: str) -> int:
        n = 0
        for tok in _PAT.findall(text):
            tok = "".join(self.byte_enc[b] for b in tok.encode("utf-8"))
            n += len(self._bpe(tok).split(" "))
        return n


def get_tokenizer():
    try:  # exact modern tokenizer when files are cached (local runs)
        import tiktoken
        enc = tiktoken.get_encoding("o200k_base")
        enc.encode("probe")

        class TT:
            name = "tiktoken/o200k_base (exact)"

            @staticmethod
            def count(s: str) -> int:
                return len(enc.encode(s))
        return TT()
    except Exception:
        return GPT2BPE()


# ------------------------------------------------------------- baselines ---

def json_pretty(recs):
    return json.dumps(recs, indent=2)


def json_compact(recs):
    return json.dumps(recs, separators=(",", ":"))


def yaml_dump(recs):
    import yaml
    return yaml.safe_dump(recs, sort_keys=False)


def xml_dump(recs, item="rec"):
    def cell(k, v):
        s = ("true" if v is True else "false" if v is False
             else "" if v is None else str(v))
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<{k}>{s}</{k}>"
    body = "".join(f"<{item}>" + "".join(cell(k, v) for k, v in r.items())
                   + f"</{item}>" for r in recs)
    return f"<records>{body}</records>"


def toon_like(recs):
    """TOON-style tabular encoding: keys declared once PER MESSAGE.
    (PACT's delta: keys move off-wire entirely, into the cached schema.)"""
    keys = list(recs[0].keys())

    def esc(v):
        if v is True:
            return "true"
        if v is False:
            return "false"
        if v is None:
            return "null"
        return str(v).replace(",", "\\,").replace("\n", "\\n")
    lines = [f"items[{len(recs)}]{{{','.join(keys)}}}:"]
    lines += ["  " + ",".join(esc(r[k]) for k in keys) for r in recs]
    return "\n".join(lines)
