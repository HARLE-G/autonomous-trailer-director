"""Small, deterministic text utilities shared by story-map builder and verifier."""
import re

WORD = re.compile(r"[a-z']+")


def kin_terms(text, kin_map):
    return {kin_map[w] for w in WORD.findall((text or "").lower()) if w in kin_map}


def word_hits(text, terms):
    return [t for t in terms if re.search(rf"\b{re.escape(t)}\b", text or "", re.I)]


def regex_hits(text, patterns):
    return [p for p in patterns if re.search(p, text or "", re.I)]


def shouting(text):
    return len(re.findall(r"\b[A-Z][A-Z']{2,}\b", text or "")) >= 3


def norm(s):
    return " ".join((s or "").split())


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
