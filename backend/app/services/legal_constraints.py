"""Stage 2 — deterministic legal constraint pass.

The candidate set from Stage 1 is recall-oriented and cross-chapter, so it
necessarily contains codes that the tariff's own Section/Chapter Notes forbid
for the goods in question. This stage mechanically pulls the governing notes
for each candidate's chapter, encodes the *encodable* exclusion notes as
machine-checkable rules, prunes candidates that violate a hard exclusion, and
attaches a "legal docket" (the controlling note text + which candidates it
bears on) for the Stage 3 GIR adjudicator.

Two sources of constraints:

  1. Data-driven exclusions parsed from ``tariff_chapters.parquet``
     ``chapter_notes``: the recurrent "This Chapter does not cover ... of
     heading XXXX" pattern. When a candidate's chapter note explicitly routes
     a class of goods to *another* heading, and the item description matches
     the excluded class, the in-chapter candidate is a docket note (advisory),
     never a silent prune — these are too contextual to prune mechanically.

  2. A small table of hard structural rules that survive a CESTAT challenge —
     e.g. Section XVII Note 2(f), which excludes electrical machinery/equipment
     of Chapter 85 from the "parts" headings of vehicles/aircraft/ships
     (8708, 8714, 8803, 8907 ...). A vehicle wiring harness is therefore 8544
     (Ch.85), never an 8708 "parts" code. These ARE hard prunes when the
     antecedent matches, with a pinpoint citation.

Conservative by design: only prune on a clear, text-supported exclusion whose
antecedent the description satisfies; otherwise attach the note and let the LLM
adjudicate.
"""
from __future__ import annotations

import re
import threading

import pandas as pd

from backend.app.config import settings
from backend.app.core.logger import get as get_logger
from backend.app.core.text_cleaning import token_set_for_overlap

log = get_logger(__name__)

_LOCK = threading.Lock()
_CHAPTER_NOTES: dict[int, dict] | None = None

# "... of heading 8544" / "... of headings 8501 to 8504" / "heading 8544 or 8536"
_HEADING_REF_RE = re.compile(r"headings?\s+((?:\d{4}[\s,/或and to]*)+)", re.IGNORECASE)
_DOES_NOT_COVER_RE = re.compile(r"(does not (?:cover|apply|include)|excludes?)", re.IGNORECASE)


# --- Hard structural rules ---------------------------------------------------
# Each rule: when a candidate sits in one of ``bad_headings`` (4-digit) AND the
# description satisfies ``antecedent`` (any trigger token present and no
# blocker), the candidate is PRUNED with ``citation``. ``redirect_chapters``
# documents where the goods belong (used to phrase the docket, not enforced).
HARD_RULES: tuple[dict, ...] = (
    {
        "id": "SEC_XVII_NOTE_2_ELECTRICAL_PARTS",
        "citation": (
            "Section XVII Note 2(f): the expression 'parts' and 'parts and "
            "accessories' in Chapters 86-89 does not apply to electrical "
            "machinery or equipment of Chapter 85. Wiring sets/harnesses and "
            "electrical goods are classified in Chapter 85 on their own terms, "
            "not as vehicle/aircraft/ship 'parts'."
        ),
        # 'Parts' headings of Section XVII that wrongly attract electrical goods.
        "bad_headings": {"8708", "8714", "8803", "8807", "8908", "8907", "8487"},
        "trigger_tokens": {
            "wiring", "harness", "loom", "cable", "wire", "connector",
            "ignition", "electrical", "electric", "switch", "relay",
            "sensor", "ecu", "alternator", "ignit",
        },
        "redirect_chapters": {85},
    },
    {
        "id": "CH85_PARTS_NOT_FROM_SEC_XVII",
        "citation": (
            "Chapter 85 / Section XVI Note 2: electrical apparatus and its "
            "parts are classified within Chapter 85; an electrical good is not "
            "a Section XVII vehicle 'part' merely because it is fitted to a "
            "vehicle."
        ),
        "bad_headings": set(),  # documentation-only companion to the rule above
        "trigger_tokens": set(),
        "redirect_chapters": {85},
    },
)

# ITA-1 / passive-component scope guard used by Stage 5: connectors and passive
# automotive electrical components frequently carry a legitimate 0% / bound
# rate. The legal layer exposes the predicate; the revenue layer consumes it.
ITA1_SCOPE_TOKENS = {
    "connector", "connectors", "contact", "terminal", "terminals",
    "coaxial", "co-axial", "passive", "resistor", "capacitor", "inductor",
    "diode", "transistor", "semiconductor",
}


def _load_chapter_notes() -> dict[int, dict]:
    global _CHAPTER_NOTES
    with _LOCK:
        if _CHAPTER_NOTES is not None:
            return _CHAPTER_NOTES
        path = settings.data_dir / "tariff_chapters.parquet"
        if not path.exists():
            _CHAPTER_NOTES = {}
            return _CHAPTER_NOTES
        df = pd.read_parquet(path)
        by_chapter: dict[int, dict] = {}
        for r in df.itertuples(index=False):
            try:
                ch = int(getattr(r, "chapter_no"))
            except (TypeError, ValueError):
                continue
            notes = str(getattr(r, "chapter_notes", "") or "").strip()
            section_notes = str(getattr(r, "section_notes", "") or "").strip()
            full = (notes + "\n" + section_notes).strip()
            by_chapter[ch] = {
                "chapter_no": ch,
                "section_no": int(getattr(r, "section_no", 0) or 0),
                "title": str(getattr(r, "title", "") or ""),
                "notes": full,
                "exclusions": _parse_exclusions(full),
            }
        _CHAPTER_NOTES = by_chapter
        return _CHAPTER_NOTES


def _parse_exclusions(notes: str) -> list[dict]:
    """Extract 'does not cover ... heading XXXX' exclusion clauses.

    Returns clauses of ``{"text": <sentence>, "headings": {<4digit>...}}`` —
    each says "goods described in <text> belong to <headings>, not this
    chapter". We split on the exclusion verb and keep the trailing clause(s)
    that carry heading cross-references.
    """
    if not notes:
        return []
    out: list[dict] = []
    for sentence in re.split(r"(?<=[.;])\s+", notes):
        if not _DOES_NOT_COVER_RE.search(sentence):
            continue
        # A single "does not cover" sentence often lists several (a)/(b)/(c)
        # excluded classes, each ending in a heading reference. Split on the
        # lettered enumerators so each excluded class keeps its own headings.
        for clause in re.split(r"\([a-z]\)", sentence):
            headings = _headings_in(clause)
            if headings:
                out.append({"text": clause.strip()[:400], "headings": headings})
        if not any(_headings_in(c) for c in re.split(r"\([a-z]\)", sentence)):
            headings = _headings_in(sentence)
            if headings:
                out.append({"text": sentence.strip()[:400], "headings": headings})
    return out


def _headings_in(text: str) -> set[str]:
    out: set[str] = set()
    for m in _HEADING_REF_RE.finditer(text):
        for h in re.findall(r"\d{4}", m.group(1)):
            out.add(h)
    return out


def _desc_tokens(description: str) -> set[str]:
    low = (description or "").lower()
    raw = {t for t in low.replace("-", " ").split() if t}
    # Also keep hyphen-joined forms ("co-axial" -> "coaxial") so compound
    # product terms survive tokenisation.
    joined = {t for t in low.replace("-", "").split() if t}
    return raw | joined | token_set_for_overlap(description)


def _hard_rule_violation(cth: str, desc_tokens: set[str]) -> dict | None:
    heading = (cth or "")[:4]
    for rule in HARD_RULES:
        if heading not in rule["bad_headings"]:
            continue
        if not rule["trigger_tokens"]:
            continue
        if desc_tokens & rule["trigger_tokens"]:
            return rule
    return None


def in_ita1_scope(description: str) -> bool:
    """True when the item reads as an ITA-1 / passive electrical component.

    Stage 5 uses this to bar raising a revenue finding on connector / passive
    automotive scenarios that legitimately carry a bound 0% rate.
    """
    return bool(_desc_tokens(description) & ITA1_SCOPE_TOKENS)


def apply(
    item_description: str,
    candidates: list[dict],
    declared_cth: str | None = None,
) -> dict:
    """Run the legal constraint pass over Stage-1 candidates.

    Returns ``{"candidates": [...surviving, annotated...], "docket": [...],
    "pruned": [...], "summary": str}``. Surviving candidates gain a
    ``legal_notes`` list (advisory note text) and a ``legal_pruned`` flag is
    never set on survivors. Pruned candidates are removed from the returned
    candidate list but reported in ``pruned`` with the citation, UNLESS the
    candidate is the importer's declared CTH — that is retained (flagged) so
    the adjudicator can still rebut it explicitly.
    """
    notes_by_chapter = _load_chapter_notes()
    desc_tokens = _desc_tokens(item_description)
    declared_cth = (declared_cth or "").strip()

    surviving: list[dict] = []
    pruned: list[dict] = []
    docket: list[dict] = []
    docket_seen: set[str] = set()

    cand_headings = {(c.get("cth") or "")[:4] for c in candidates}

    for c in candidates:
        cth = (c.get("cth") or "").strip()
        try:
            chap = int(c.get("chapter_no") or int(cth[:2]))
        except (TypeError, ValueError):
            chap = int(cth[:2]) if cth[:2].isdigit() else 0
        legal_notes: list[str] = []

        # 1) Hard structural rule.
        violation = _hard_rule_violation(cth, desc_tokens)

        # 2) Data-driven chapter-note exclusions: if this chapter's note routes
        #    a matching class of goods to ANOTHER heading that is also a
        #    candidate, attach as advisory docket (don't prune — too
        #    contextual).
        chap_meta = notes_by_chapter.get(chap)
        if chap_meta:
            for exc in chap_meta["exclusions"]:
                exc_tokens = token_set_for_overlap(exc["text"])
                overlap = desc_tokens & exc_tokens
                routes_to_candidate = exc["headings"] & cand_headings
                if len(overlap) >= 2 and routes_to_candidate:
                    key = f"{chap}:{sorted(exc['headings'])}"
                    note = (
                        f"Chapter {chap} note excludes goods of this kind: "
                        f"\"{exc['text']}\" → see heading(s) "
                        f"{', '.join(sorted(routes_to_candidate))}."
                    )
                    legal_notes.append(note)
                    if key not in docket_seen:
                        docket_seen.add(key)
                        docket.append({
                            "rule_id": f"CH{chap}_EXCLUSION",
                            "citation": note,
                            "bears_on": [cth],
                            "kind": "advisory",
                        })

        if violation is not None:
            entry = {
                "cth": cth,
                "rule_id": violation["id"],
                "citation": violation["citation"],
            }
            docket.append({
                "rule_id": violation["id"],
                "citation": violation["citation"],
                "bears_on": [cth],
                "kind": "hard_exclusion",
            })
            is_declared = cth == declared_cth or c.get("source") == "declared_cth"
            if is_declared:
                # Keep the importer's own code so the LLM rebuts it on record,
                # but mark it as legally excluded.
                c = {**c, "legal_pruned": True,
                     "legal_notes": legal_notes + [violation["citation"]]}
                surviving.append(c)
                pruned.append(entry)
            else:
                pruned.append(entry)
            continue

        surviving.append({**c, "legal_pruned": False, "legal_notes": legal_notes})

    summary_bits = []
    if pruned:
        summary_bits.append(
            f"{len(pruned)} candidate(s) hit a hard exclusion "
            f"({', '.join(sorted({p['rule_id'] for p in pruned}))})"
        )
    if docket:
        summary_bits.append(f"{len(docket)} legal note(s) on docket")
    if not summary_bits:
        summary_bits.append("no governing exclusion notes bear on the candidate set")

    return {
        "candidates": surviving,
        "docket": docket,
        "pruned": pruned,
        "summary": "; ".join(summary_bits),
    }


def preload() -> None:
    _load_chapter_notes()
