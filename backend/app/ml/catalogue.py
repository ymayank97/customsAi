"""High-precision catalogue evidence, separate from uncalibrated ML scores.

Only a unique, explicit positive leaf phrase can be accepted automatically.
Generic Other buckets, exclusions, missing specifications and accessory matches
cannot pass that gate. Broader evidence can support a heading without inventing
eight-digit precision. No importer-declared code is used here.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from backend.app.core.text_cleaning import clean_description

GENERIC = set(ENGLISH_STOP_WORDS) | {
    "other", "goods", "article", "part", "material", "type", "kind", "equipment",
    "plastic", "steel", "rubber", "wood", "copper", "aluminium", "aluminum",
    "electric", "electrical", "electronic", "powered", "operated", "suitable",
}
_EXCLUSION = re.compile(r"\b(?:other than|excluding|except|not)\b", re.I)
_DEPENDENT = re.compile(r"\b(?:for|with|without|whether|including|used|fitted|suitable)\b", re.I)
_CONDITION = re.compile(r"\b(?:for|of|with|without|not|whether|than|exceeding|containing|incorporating|weighing|assembled|unassembled)\b|\d", re.I)


def tokens(text):
    text = clean_description(text).replace("-", " ").replace("&", " and ")
    words = re.findall(r"[a-z]+(?:\d+[a-z]*)?|\d+(?:\.\d+)?[a-z]*", text)
    # A small, deterministic plural normalization; dimensions and grades remain
    # intact in the original description and in the ML feature pipeline.
    return [word[:-3] + "y" if len(word) > 4 and word.endswith("ies") else
            word[:-2] if word.endswith(("sses", "xes", "ches", "shes", "zzes")) or word == "lenses" else
            word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")) else word
            for word in words]


def positive_head(text):
    # Parenthesized examples are additional positive nouns in heading wording.
    examples = re.findall(r"\(\s*for example[,:]?\s*([^)]*)\)", text, re.I)
    main = re.sub(r"\([^)]*\)", " ", text)
    main = _EXCLUSION.split(main, maxsplit=1)[0]
    main = _DEPENDENT.split(main, maxsplit=1)[0]
    return " ".join([main, *examples])


def phrase_variants(description):
    # 'including ...' elaborates the named goods; specifications in the main
    # description, unlike examples, must never be silently dropped.
    main = re.split(r"\bincluding\b", description, maxsplit=1, flags=re.I)[0]
    main = re.sub(r"\([^)]*\)", " ", main)
    if _EXCLUSION.search(main) or _CONDITION.search(main):
        return []
    variants = []
    for phrase in re.split(r"[,;]|\band\b|\bor\b", main, flags=re.I):
        words = tokens(phrase)
        meaningful = [word for word in words if word not in GENERIC]
        if len(meaningful) >= 2 and len(words) >= 2:
            variants.append(tuple(words))
    return variants


def parent_requirements_met(row, description):
    heading = str(row.get("heading_text") or "") + " " + str(row.get("subheading_text") or "")
    bound = re.search(r"voltage\s+(not exceeding|exceeding)\s+([\d,]+)\s*volts?", heading, re.I)
    if bound:
        readings = [(float(number) * (1000 if unit.lower() == "kv" else 1))
                    for number, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(kv|volts?|v)\b", description, re.I)]
        if not readings:
            return False
        limit = float(bound[2].replace(",", ""))
        valid = all(voltage <= limit for voltage in readings) if bound[1].lower() == "not exceeding" else all(voltage > limit for voltage in readings)
        if not valid:
            return False
        heading = heading[:bound.start()] + heading[bound.end():]
    # Other inherited quantitative conditions need a dedicated attribute check.
    if re.search(r"\b(?:weighing|not exceeding|less than|of a width|containing[^;]*%)\b", heading, re.I):
        return False
    return True


class CatalogueEvidence:
    def __init__(self, rows):
        self.rows = {str(row["cth"]): row for row in rows}
        phrases = defaultdict(set)
        self.heading_terms = defaultdict(set)
        for code, row in self.rows.items():
            for phrase in phrase_variants(str(row.get("description") or "")):
                phrases[phrase].add(code)
            self.heading_terms[code[:4]].update(
                word for word in tokens(positive_head(str(row.get("heading_text") or "")))
                if word not in GENERIC)
        self.phrases = {phrase: sorted(codes) for phrase, codes in phrases.items()}
        self.first_word = defaultdict(list)
        for phrase in self.phrases:
            self.first_word[phrase[0]].append(phrase)
        path = Path(__file__).with_name("catalogue_aliases.json")
        self.aliases = json.loads(path.read_text(encoding="utf-8"))

    def inspect(self, description):
        head = re.split(r"\b(?:for|used|with|fitted)\b", description, maxsplit=1, flags=re.I)[0]
        words = tokens(head)
        # Negation or alternative product descriptions require clarification.
        negative = bool(re.search(r"\b(?:not|without|excluding|except|or|and)\b", description, re.I))
        matches = []
        for offset, word in enumerate(words):
            for phrase in self.first_word.get(word, []):
                if tuple(words[offset:offset + len(phrase)]) == phrase:
                    # A mention inside an accessory's name is not the goods.
                    prefix = [w for w in words[:offset] if not any(c.isdigit() for c in w)]
                    if prefix and any(w not in GENERIC for w in prefix):
                        continue
                    matches.append((phrase, self.phrases[phrase]))
        # Prefer the full named article over a component prefix, e.g. LED
        # modules rather than the diode named at the start of that phrase.
        matches = [(phrase, found) for phrase, found in matches
                   if not any(len(longer) > len(phrase) and longer[:len(phrase)] == phrase
                              for longer, _ in matches)]
        codes = set(code for _, found in matches for code in found)
        if len(codes) == 1 and not negative:
            code = next(iter(codes))
            qualified_use = bool(re.search(r"\b(?:for|with|used|fitted)\b", description, re.I))
            if qualified_use or not parent_requirements_met(self.rows[code], description):
                return {"accepted": "", "prefix": code[:4], "basis": "missing_parent_specification",
                        "matched_phrase": "", "headings": [code[:4]]}
            phrase = max((phrase for phrase, found in matches if code in found), key=len)
            return {"accepted": code, "prefix": code, "basis": "unique_positive_tariff_phrase",
                    "matched_phrase": " ".join(phrase), "headings": [code[:4]]}
        all_words = set(tokens(description))
        for alias in self.aliases:
            if (set(alias["all_tokens"]).issubset(all_words)
                    and set(alias["context_any"]) & all_words):
                headings = [h for h in alias["headings"] if h in self.heading_terms]
                if headings:
                    return {"accepted": "", "prefix": headings[0] if len(headings) == 1 else headings[0][:2],
                            "basis": "sourced_heading_terminology", "matched_phrase": "",
                            "headings": headings, "source": alias["source"]}
        # Exact whole-word heading nouns keep 'connector' out of a copper pipe
        # fitting whose leaf happens to mention an accessory, and prevent
        # character fragments such as Terminal/Terminalia driving a decision.
        # Last content word in the goods phrase is usually its noun; preceding
        # words can be attributes ('2 pole connector' is not a wooden pole).
        content = [word for word in words if word not in GENERIC and word.isalpha()]
        query, context = set(content[-1:]), set(content)
        scored = [((len(query & terms), len(context & terms)), heading)
                  for heading, terms in self.heading_terms.items()]
        best = max((count for count, _ in scored), default=(0, 0))
        headings = sorted(heading for count, heading in scored if best[0] and count == best)
        prefix = ""
        if headings:
            common = headings[0]
            while common and not all(heading.startswith(common) for heading in headings):
                common = common[:-1]
            prefix = common[:len(common) // 2 * 2]
        return {"accepted": "", "prefix": prefix, "basis": "heading_terms" if headings else "insufficient_evidence",
                "matched_phrase": "", "headings": headings}
