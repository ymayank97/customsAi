"""Text cleaning / normalisation for tariff item descriptions.

Customs Bills of Entry contain very noisy free-text:
    - Unicode punctuation, smart quotes, NBSP, soft hyphens, BOMs
    - HSN/CTH codes embedded ("HSN: 84713010", "CTH: 8471 30 10")
    - Shipment count fragments ("100 PCS", "QTY: 50 NOS")
    - Repeated boilerplate ("MAKE IN INDIA", "AS PER INVOICE", "FOC", "FREIGHT")
    - Mixed casing, multi-spaces, trailing colons / dashes

These confuse both TF-IDF and BERT chapter classifiers. This module exposes
``clean_description`` which produces a stable canonical form suitable for both
training data and inference queries.

Product measurements, composition, end use, and part/model identifiers are
classification evidence and must survive cleaning. Only explicit declaration
codes, shipment counts, and known boilerplate are removed. It is idempotent
and pure.
"""
from __future__ import annotations

import re
import threading
import unicodedata
from typing import Iterable

# Lazy Porter stemmer (no extra NLTK corpora downloads).
_STEMMER = None
_STEMMER_LOCK = threading.Lock()

# Common Bill-of-Entry / tariff abbreviations → expanded form (substrings safe on word boundaries).
_ABBREV_EXPANSIONS: tuple[tuple[str, str], ...] = (
    (r"\bassy\b", "assembly"),
    (r"\bassys\b", "assemblies"),
    (r"\bassy\.\b", "assembly"),
    (r"\bpcs\b", "pieces"),
    (r"\bpc\b", "piece"),
    (r"\bqty\b", "quantity"),
    (r"\bwt\b", "weight"),
    (r"\bdiag\b", "diagonal"),
    (r"\bmax\b", "maximum"),
    (r"\bmin\b", "minimum"),
    (r"\bapprox\b", "approximately"),
    (r"\bref\b", "reference"),
    (r"\bstd\b", "standard"),
    (r"\bvolts?\b", "volt"),
    (r"\bwatts?\b", "watt"),
    (r"\bno?s\.?\b", "number"),
    (r"\bmtg\b", "mounting"),
    (r"\bconn\b", "connector"),
    (r"\bconns\b", "connectors"),
)


# Unicode punctuation -> ASCII equivalents we want to normalise away.
_PUNCT_MAP = {
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2010": "-", "\u2011": "-", "\u2012": "-",
    "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
    "\u201C": '"', "\u201D": '"', "\u201E": '"', "\u201F": '"',
    "\u00A0": " ",  # NBSP
    "\u200B": "",   # zero-width space
    "\u200C": "", "\u200D": "", "\uFEFF": "",
    "\u2022": " ",  # bullet
    "\u00B7": " ",
}

# Boilerplate fragments we strip wholesale (case-insensitive substring match).
_BOILERPLATE = [
    r"as\s*per\s*invoice", r"as\s*per\s*packing\s*list", r"make\s*in\s*india",
    r"freight\s*charges?", r"insurance\s*charges?", r"foc\b",
    r"\bfor\s*reference\s*only\b", r"\bsample\s*not\s*for\s*sale\b",
    r"\bno\s*commercial\s*value\b", r"\bgift\s*item\b",
    r"\bp\.?o\.?\s*no\.?\s*[a-z0-9\-/]+",
    r"\binvoice\s*no\.?\s*[a-z0-9\-/]+",
    r"\bb/?l\s*no\.?\s*[a-z0-9\-/]+",
    # Customs-specific noise that obscures the real head/modifier split.
    r"\bfor\s*captive\s*(?:consumption|use|requirement)\b",
    r"\bfor\s*own\s*(?:consumption|use|requirement)\b",
    r"\bfor\s*resale\b",
    r"\bfor\s*trading\s*purpose\b",
]

# Remove explicit piece counts, never physical measurements or product nouns
# such as pairs, sets, rolls, and bottles. A weight/capacity may be decisive.
_COUNT_UNIT_TOKENS = "pcs|pieces?|nos?|units?"
_QTY_RE = re.compile(rf"\b\d+(?:[.,]\d+)?\s*(?:{_COUNT_UNIT_TOKENS})\b", re.IGNORECASE)
_QTY_PREFIX_RE = re.compile(
    rf"\b(?:qty|quantity)\s*[:\-]?\s*\d+(?:[.,]\d+)?\s*(?:{_COUNT_UNIT_TOKENS})\b",
    re.IGNORECASE,
)

# Embedded HSN/CTH/HS codes.
_CODE_RE = re.compile(
    r"\bchapter\s*[:\-]?\s*\d{2}(?!\d)|"
    r"\b(?:hsn|hs\s*code|cth|customs\s*tariff(?:\s*heading)?)\s*[:\-]?\s*"
    r"(?:\d{4}(?:[. ]+\d{2}(?![\w%]|\s*%)){1,2}|"
    r"\d{2}(?:[. ]+\d{2}(?![\w%]|\s*%)){1,3}|\d{2,10}(?![\w%]))",
    re.IGNORECASE,
)
# Preserve decimal punctuation, percentages, dimensions, ranges, and comparison
# operators. Flatten sentence punctuation without turning 0.5 into "0 5".
_PUNCT_FLATTEN_RE = re.compile(r"[(){}\[\];:!?\"'`~|]")
_LEADING_DECIMAL_RE = re.compile(r"(?<![\w.])\.(?=\d)")
_NON_NUMERIC_PUNCT_RE = re.compile(r"(?<!\d)[.,]|[.,](?!\d)")

# Multiple spaces or stray dashes.
_MULTI_SPACE_RE = re.compile(r"\s+")
_DASHES_RE = re.compile(r"(?<!\w)-+(?!\w)")
_LEADING_TRAILING_PUNCT_RE = re.compile(r"^[\-\s/]+|[\-\s/]+$")


def _normalise_unicode(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for k, v in _PUNCT_MAP.items():
        if k in s:
            s = s.replace(k, v)
    return s


def clean_description(text: str | None) -> str:
    """Return a canonical lowercase description with structural noise removed.

    Idempotent. Safe on ``None`` / empty / non-string inputs.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text.strip():
        return ""

    s = _normalise_unicode(text).lower()

    # Strip only explicitly labelled codes and piece counts. Bare numbers and
    # mixed alphanumeric tokens can be specifications, grades, or identifiers.
    s = _CODE_RE.sub(" ", s)
    s = _QTY_PREFIX_RE.sub(" ", s)
    s = _QTY_RE.sub(" ", s)
    for pat in _BOILERPLATE:
        s = re.sub(pat, " ", s, flags=re.IGNORECASE)

    # Keep decimal values, including the common shorthand ".5 mm".
    s = _LEADING_DECIMAL_RE.sub("0.", s)
    s = _NON_NUMERIC_PUNCT_RE.sub(" ", s)
    s = _PUNCT_FLATTEN_RE.sub(" ", s)
    s = _DASHES_RE.sub(" ", s)

    # Discard stray punctuation, not product specifications.
    tokens = []
    for tok in s.split():
        if not any(ch.isalnum() for ch in tok) and tok not in {"<", ">", "<=", ">=", "=", "+", "%", "×"}:
            continue
        tokens.append(tok)
    s = " ".join(tokens)

    s = _MULTI_SPACE_RE.sub(" ", s)
    s = _LEADING_TRAILING_PUNCT_RE.sub("", s)
    return s.strip()


def clean_many(texts: Iterable[str | None]) -> list[str]:
    return [clean_description(t) for t in texts]


def expand_abbreviations(text: str) -> str:
    """Expand a small domain abbreviation table after :func:`clean_description`."""
    if not text:
        return ""
    s = text
    for pat, repl in _ABBREV_EXPANSIONS:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    s = _MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def _get_stemmer():
    global _STEMMER
    with _STEMMER_LOCK:
        if _STEMMER is None:
            try:
                from nltk.stem import PorterStemmer
                _STEMMER = PorterStemmer()
            except Exception:
                _STEMMER = False  # type: ignore[assignment]
        return _STEMMER if _STEMMER else None


def stem_token(word: str) -> str:
    """Stem a single token; pass-through when NLTK is unavailable."""
    if not word or len(word) < 2:
        return word
    st = _get_stemmer()
    if st is None:
        return word
    try:
        return st.stem(word)
    except Exception:
        return word


def normalize_for_matching(text: str | None) -> str:
    """Lower-noise text for lexical overlap / BM25 / condition scoring.

    Pipeline: ``clean_description`` → ``expand_abbreviations`` → join stemmed
    tokens (Porter).  Falls back to cleaned text only if stemming is
    unavailable.
    """
    base = expand_abbreviations(clean_description(text)) or ""
    if not base:
        return ""
    # BM25 and alias keys must also retain decimals and single-digit values or
    # units; otherwise descriptions distinguished by cleaning collapse again.
    tokens = re.findall(r"[<>]=?|=|[a-z0-9]+(?:[.,]\d+)*%?", base.lower())
    stems = [stem_token(t) for t in tokens]
    return " ".join(stems)


def token_set_for_overlap(text: str | None) -> set[str]:
    """Informative stems and numeric specifications for condition matching."""
    s = normalize_for_matching(text)
    return {t for t in s.split() if len(t) >= 3 or any(ch.isdigit() for ch in t)}


# ----------------------------------------------------------------------------
# Head-noun extraction for cross-encoder reranking.
#
# Customs item descriptions are dominated by the modifier "FOR / OF / USED IN
# X" pattern (e.g. "CONNECTOR FOR WIRE AND CABLE", "PARTS FOR LOUD SPEAKER",
# "BEARING USED IN AUTOMOTIVE GEARBOX").  A generic web-search reranker treats
# every word equally and over-weights the modifier (X), so it surfaces the
# heading that *makes* X instead of the heading that classifies the head noun.
#
# These helpers split a query into (head, modifier) on the first preposition
# that introduces a modifier, then build a boosted query that repeats the
# head so cross-encoders give it primary attention.
# ----------------------------------------------------------------------------

# Prepositions / phrases that introduce a *modifier* of the head noun.  Order
# longest-first so multi-word phrases match before bare "for".
#
# We deliberately omit a bare "of" — in customs vocabulary "X of Y" almost
# always means "X made of Y" (e.g. "PARTS OF STEEL", "ARTICLES OF PLASTIC",
# "BEARING OF RUBBER") where the material is part of the head noun phrase
# and shouldn't be stripped.  The longer "X of Y for Z" cases still split
# correctly on the "for".
_MODIFIER_INTRODUCERS = (
    "to be used for", "to be used in", "to be used with",
    "used solely for", "used solely with",
    "for use with", "for use in", "for use as",
    "suitable for", "compatible with",
    "used with", "used for", "used in",
    "part for", "parts for",
    "components for",
    "for",
)

_HEAD_NOUN_RE = re.compile(
    r"^(?P<head>.+?)\s+(?P<intro>"
    + "|".join(re.escape(p) for p in _MODIFIER_INTRODUCERS)
    + r")\s+(?P<mod>.+)$",
    re.IGNORECASE,
)


def extract_head_noun(query: str) -> tuple[str, str]:
    """Heuristically split ``"X for Y"`` / ``"X used in Y"`` patterns.

    Returns ``(head, modifier)`` where ``head`` is the primary noun phrase
    (before the first modifier-introducer) and ``modifier`` is whatever
    came after.  Returns ``(cleaned_query, "")`` when no pattern is found.

    Both pieces are cleaned with :func:`clean_description` first so shipment
    counts and boilerplate don't bleed into the split; specifications remain.
    """
    if not query:
        return ("", "")
    cleaned = clean_description(query) or query.lower().strip()
    if not cleaned:
        return ("", "")

    m = _HEAD_NOUN_RE.match(cleaned)
    if not m:
        return (cleaned, "")
    head = m.group("head").strip(" -/")
    mod = m.group("mod").strip(" -/")
    # Reject splits where the head is too short to be meaningful (e.g.
    # "for captive use" alone — the cleaner already removed boilerplate
    # but defensive).
    if len(head) < 3 or not any(ch.isalpha() for ch in head):
        return (cleaned, "")
    return (head, mod)


def boost_head_noun(query: str) -> str:
    """Build a head-noun-emphasised version of the query for reranking.

    The boosted query repeats the head noun so cross-encoders weight it
    twice, while preserving the modifier as supplemental context:

        "CONNECTOR FOR WIRE AND CABLE FOR CAPTIVE CONSUMPTION"
            ↓  clean_description
        "connector contact element for wire and cable"
            ↓  extract_head_noun
        head="connector contact element"   modifier="wire and cable"
            ↓  boost_head_noun
        "connector contact element. connector contact element for wire and cable."

    Falls back to the cleaned query when no modifier pattern is found.
    """
    head, mod = extract_head_noun(query)
    if not head:
        return query or ""
    if not mod:
        return head
    return f"{head}. {head} for {mod}."


__all__ = [
    "clean_description",
    "clean_many",
    "expand_abbreviations",
    "normalize_for_matching",
    "stem_token",
    "token_set_for_overlap",
    "extract_head_noun",
    "boost_head_noun",
]
