"""LLM adapter: Claude (primary) + Gemini (fallback) + heuristic offline mode.

Provides a unified API for:
  - classify_batch(items)            — Tier 3 batched classification
  - reevaluate(item)                 — single-item, stricter classification
  - chat_stream(question, context)   — SSE-friendly streaming Q&A

If no API key is set, falls back to a deterministic 'best retrieval candidate'
classifier so the pipeline still runs end-to-end without LLM credentials.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Iterator, Protocol

from backend.app.config import settings
from backend.app.core.logger import get as get_logger
from backend.app.schemas.classification import ClassificationResult

log = get_logger(__name__)

SYSTEM_INSTRUCTION = """You are a senior Indian Customs adjudicator writing a speaking order. You are NOT performing 12,000-way classification. You are doing ADVERSARIAL VERIFICATION: the importer declared a CTH; given the description and a set of legally-vetted candidate codes, decide the strongest legally-supported tariff item and walk the General Interpretative Rules (GIR 1→6) explicitly to justify it.

Indian customs codes are strictly hierarchical: 1-2 = Chapter, 1-4 = Heading, 1-6 = HS sub-heading, 1-8 = tariff item.

EVERY candidate supplied to you is a REAL code from the First Schedule. You MUST choose ``cth`` from the supplied candidates — never invent or "correct" a code to one not in the list. If no candidate fits, choose the closest candidate and lower your confidence; do not fabricate.

Walk these rules in order, and state each explicitly in ``reasoning``:

  GIR 1 — Headings and the relevant Section/Chapter Notes are the primary basis.
    Quote the heading text. Read the LEGAL DOCKET supplied with the item: it
    lists Section/Chapter exclusion notes that bear on specific candidates. If a
    note in the docket EXCLUDES a candidate for these goods, you MUST NOT pick
    it — cite the note and rule it out. (e.g. Section XVII Note 2 excludes
    electrical machinery of Chapter 85 from vehicle "parts" headings like 8708,
    so a vehicle wiring harness is heading 8544, never 8708.)
  GIR 2(a) — Incomplete/unassembled goods having the essential character of the
    complete article. Use the sibling lines on the same Bill of Entry (supplied
    as ``be_siblings``) to judge whether the line is a knocked-down part of a
    larger article.
  GIR 3(a) — The most specific heading prevails over the more general.
  GIR 3(b) — For composite goods/sets, classify by the component that gives
    essential character.
  GIR 3(c) — When 3(a)/(b) do not resolve, the heading occurring last in
    numerical order.
  GIR 6 — Sub-heading selection: compare only sub-headings at the same level
    within the chosen heading.

CRITICAL LEAF-TEXT RULE: a 6-digit sub-heading description can be misleading. Before committing to an 8-digit ``cth``, VERIFY the candidate's ``leaf_description`` (the actual schedule text of the 8-digit item) describes these goods. Sub-headings lie; the 8-digit leaf text is authoritative. If the specific leaf does not name the goods, use the heading's residuary "other" leaf and set ``is_residuary`` true.

The importer's declared code is tagged ``is_declared_by_importer: true`` (and may be tagged ``legally_excluded: true`` if a docket note already rules it out). Treat it as the hypothesis to CONFIRM or REBUT explicitly.

AMBIGUITY SET: besides the chosen ``cth``, output ``conformal_set`` — the list of candidate codes you cannot rule out for these goods (include the chosen code). This field is an uncalibrated ambiguity set, with no statistical coverage guarantee. Do not infer high confidence from a singleton when product facts are missing.

OUTPUT — a JSON array, one object per input id, with these exact keys:
  • id              (string)
  • cth             (8-digit string — MUST be one of the supplied candidates)
  • chapter         (2-digit string)
  • heading         (4-digit string)
  • discriminator   (string — the legal rule that selected this heading/sub-heading)
  • is_residuary    (boolean)
  • attributes      (object — function, material, voltage/power, end-use, completeness)
  • bcd_rate        (number — effective % rate from the chosen candidate)
  • conformal_set   (array of 8-digit strings — candidates you cannot rule out; include cth)
  • citations       (array of strings — quote the note/heading text you relied on)
  • gir_path        (string — the GIR rules applied, e.g. "GIR1 (Sec XVII Note 2 excludes 8708) -> GIR6 leaf verified")
  • reasoning       (string — the explicit GIR walk with pinpoint citations)
  • confidence      (number 0-1)
"""

_FEWSHOT_LOCK = threading.Lock()
_FEWSHOT_CACHE: list[dict] | None = None


def _load_fewshot(n: int = 6, headings: set[str] | None = None) -> list[dict]:
    """Use only expert-labelled examples with provenance, relevant to the candidate headings."""
    global _FEWSHOT_CACHE
    with _FEWSHOT_LOCK:
        if _FEWSHOT_CACHE is None:
            path = settings.data_dir / "fewshot_examples.json"
            try:
                raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            except Exception:
                raw = []
            examples = []
            for ex in raw:
                gold = str(ex.get("gold_cth") or "").strip()
                source = str(ex.get("label_source") or "").strip()
                if len(gold) == 8 and gold.isdigit() and source:
                    examples.append({
                        "description": ex.get("description", "")[:200],
                        "correct_cth": gold,
                        "label_source": source,
                        "heading": gold[:4],
                        "leaf_text": (ex.get("leaf_text") or "")[:200],
                    })
            _FEWSHOT_CACHE = examples
    relevant = [ex for ex in _FEWSHOT_CACHE if not headings or ex["heading"] in headings]
    return relevant[:n]


CHAT_SYSTEM = """You are a senior Indian Customs auditing assistant. Use ONLY the provided CTH context to answer concisely (under 120 words). Cite specific 8-digit codes when relevant. If the context does not contain the answer, say so plainly.
"""


class LLMClassifier(Protocol):
    def classify_batch(self, items: list[dict]) -> list[ClassificationResult]: ...
    def reevaluate(self, item: dict) -> ClassificationResult: ...
    def chat_stream(self, question: str, context_items: list[dict]) -> Iterator[str]: ...


def _format_user_message(items: list[dict]) -> str:
    """Build the classifier prompt with candidates grouped by heading.

    The LLM does best when it sees the tariff hierarchy explicitly: chapter
    -> heading -> {sub-heading: leaf} so it can reason about the
    discriminator at each level instead of treating 8-digit codes as a flat
    pool.  We bucket candidates by 4-digit heading, attach the legal
    heading text, and let the candidates within each bucket carry the
    sub-heading discriminators.
    """
    payload = []
    for it in items:
        # Group the candidates by 4-digit heading.  The retriever already
        # caps at TOP_K (default 8); we don't slice again here so that the
        # *injected* declared CTH (appended after retrieval if it was
        # missed) is always preserved.
        by_heading: dict[str, dict] = {}
        for c in it.get("candidates", []):
            cth = c["cth"]
            heading = c.get("heading") or cth[:4]

            ntfns = []
            for n in c.get("notification_details", [])[:5]:
                ntfns.append({
                    "id": n.get("id"),
                    "title": (n.get("title") or "")[:160],
                    "date": n.get("date"),
                })

            cand = {
                "cth": cth,
                "subheading_6digit": c.get("subheading_6") or cth[:6],
                "is_residuary": bool(c.get("is_residuary")),
                "subheading_text": c.get("subheading_text") or "",
                "leaf_description": (c.get("leaf_text") or c.get("description") or "")[:280],
                "unit": c.get("unit"),
                "bcd_statutory": c.get("bcd_statutory"),
                "bcd_effective": c.get("bcd_effective"),
                "bcd_breakdown": c.get("bcd_breakdown"),
                "import_policy": c.get("import_policy"),
                "applicable_notifications": ntfns,
                "retrieval_score": round(float(c.get("retrieval_score") or 0.0), 4),
                # When a candidate is the importer's declared CTH (injected
                # because retrieval missed it), label it so the LLM treats
                # it as the choice to confirm or rebut.
                "is_declared_by_importer": cth == it.get("declared_cth"),
                # Stage 2 marked this candidate as ruled out by a hard
                # exclusion note (kept in the set only so the LLM rebuts it).
                "legally_excluded": bool(c.get("legal_pruned")),
                "legal_notes": (c.get("legal_notes") or [])[:3],
            }

            bucket = by_heading.get(heading)
            if bucket is None:
                bucket = {
                    "chapter_2digit": c.get("chapter") or cth[:2],
                    "chapter_title": c.get("chapter_title", ""),
                    "chapter_notes_excerpt": (c.get("chapter_notes") or "")[:240],
                    "heading_4digit": heading,
                    "heading_text": c.get("heading_text") or "",
                    "candidates": [],
                }
                by_heading[heading] = bucket
            bucket["candidates"].append(cand)

        # Sort candidates within each heading by retrieval_score (best first)
        # and sort headings by their best candidate's score.
        heading_buckets = list(by_heading.values())
        for h in heading_buckets:
            h["candidates"].sort(key=lambda c: c.get("retrieval_score") or 0.0, reverse=True)
        heading_buckets.sort(
            key=lambda h: (h["candidates"][0]["retrieval_score"] if h["candidates"] else 0.0),
            reverse=True,
        )

        payload.append({
            "id": it["id"],
            "item_description": it["item_description"],
            "declared_cth": it.get("declared_cth"),
            "declared_bcd": it.get("declared_bcd"),
            "declared_notification": it.get("declared_notification"),
            "be_siblings": it.get("be_siblings", []),
            "legal_docket": it.get("legal_docket", []),
            "candidate_headings": heading_buckets,
        })

    headings = {c["cth"][:4] for it in items for c in it.get("candidates", [])}
    fewshot = _load_fewshot(headings=headings)
    fewshot_block = (
        f"EXPERT-LABELLED EXAMPLES (with provenance, relevant to these candidate headings):\n"
        f"{json.dumps(fewshot, indent=2)}\n\n" if fewshot else ""
    )
    return (
        "Adjudicate each item by adversarial verification. Candidates are grouped by 4-digit "
        "heading so you can compare sibling sub-headings within a heading and competing headings "
        "within the chapter. Read each item's ``legal_docket`` and respect every hard exclusion; "
        "use ``be_siblings`` for GIR 2(a). Verify the 8-digit leaf_description before committing. "
        "Walk GIR 1→6 explicitly and output the structured JSON exactly as specified.\n\n"
        f"{fewshot_block}"
        f"INPUT:\n{json.dumps(payload, indent=2)}"
    )


def _parse_classification(d: dict) -> ClassificationResult:
    """Coerce a raw LLM JSON object into ``ClassificationResult``.

    Tolerates missing structured fields (chapter / heading / discriminator)
    so we don't break when the model returns the older flat schema —
    they default to derived values from the 8-digit ``cth``.
    """
    cth = str(d.get("cth", "")).strip().zfill(8)[:8]
    chapter = str(d.get("chapter") or (cth[:2] if cth else "")).strip()[:2]
    heading = str(d.get("heading") or (cth[:4] if cth else "")).strip()[:4]
    attrs = d.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {"raw": str(attrs)[:200]}

    def _str_list(v) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()][:8]
        if isinstance(v, str) and v.strip():
            return [v.strip()]
        return []

    conformal = _str_list(d.get("conformal_set"))
    conformal = [str(c).strip().zfill(8)[:8] for c in conformal]
    if cth and cth not in conformal:
        conformal.insert(0, cth)
    return ClassificationResult(
        id=str(d.get("id", "")),
        cth=cth,
        chapter=chapter,
        heading=heading,
        discriminator=str(d.get("discriminator", ""))[:400],
        is_residuary=bool(d.get("is_residuary", cth[-2:] in {"00", "90", "99"} if len(cth) == 8 else False)),
        attributes=attrs,
        bcd_rate=float(d["bcd_rate"]) if d.get("bcd_rate") is not None else None,
        reasoning=str(d.get("reasoning", "")),
        confidence=float(d.get("confidence", 0.5)),
        conformal_set=conformal,
        citations=_str_list(d.get("citations")),
        gir_path=str(d.get("gir_path", ""))[:300],
    )


def _build_chat_context(context_items: list[dict]) -> str:
    if not context_items:
        return "(no candidates retrieved)"
    lines = []
    for c in context_items[:8]:
        rate = c.get("bcd_effective") if c.get("bcd_effective") is not None else c.get("bcd_statutory")
        rate_s = f"{rate}%" if rate is not None else "—"
        desc = (c.get("description") or "")[:220]
        lines.append(f"- {c.get('cth','????????')}: {desc}  (BCD {rate_s})")
    return "\n".join(lines)


class ClaudeClassifier:
    def __init__(self):
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
       
        import httpx
        from anthropic import Anthropic

        self.client = Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        self.model = settings.claude_model

    def _sleep_backoff(self, attempt: int, err: Exception) -> float:
        resp = getattr(err, "response", None)
        headers = getattr(resp, "headers", None) or {}
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra:
            try:
                return min(float(ra), 60.0)
            except (TypeError, ValueError):
                pass
        return min(60.0, float(2**attempt))

    def classify_batch(self, items: list[dict]) -> list[ClassificationResult]:
        user_msg = _format_user_message(items)
        last_err: Exception | None = None
        t0 = time.time()
        for attempt in range(3):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=6000,
                    system=[
                        {"type": "text", "text": SYSTEM_INSTRUCTION,
                         "cache_control": {"type": "ephemeral"}},
                    ],
                    messages=[{"role": "user", "content": user_msg}],
                )
                text = "".join(b.text for b in resp.content if b.type == "text")
                start = text.find("[")
                end = text.rfind("]")
                if start < 0 or end < 0:
                    raise ValueError(f"no JSON array in response: {text[:200]}")
                data = json.loads(text[start:end + 1])
                log.info("claude.batch ok", extra={"count": len(data),
                                                    "ms": int((time.time() - t0) * 1000),
                                                    "model": self.model})
                return [_parse_classification(d) for d in data]
            except Exception as e:
                last_err = e
                code = getattr(e, "status_code", None)
                if code is None and getattr(e, "response", None) is not None:
                    code = getattr(e.response, "status_code", None)
                log.warning(f"claude.batch attempt={attempt} failed: {e}")
                if code in (401, 403, 400):
                    raise
                delay = self._sleep_backoff(attempt, e) if code == 429 else float(2**attempt)
                time.sleep(delay)
        raise RuntimeError(f"Claude failed after 3 attempts: {last_err}")

    def reevaluate(self, item: dict) -> ClassificationResult:
        """Single-item classification with stricter system prompt and lower temperature."""
        results = self.classify_batch([item])
        return results[0] if results else ClassificationResult(
            id=str(item.get("id", "")), cth="", reasoning="No response", confidence=0.0,
        )

    def chat_stream(self, question: str, context_items: list[dict]) -> Iterator[str]:
        ctx = _build_chat_context(context_items)
        user_msg = f"Question: {question}\n\nCTH context:\n{ctx}"
        with self.client.messages.stream(
            model=self.model,
            max_tokens=900,
            system=CHAT_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            for chunk in stream.text_stream:
                if chunk:
                    yield chunk


class GeminiClassifier:
    def __init__(self):
        if not settings.google_api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise RuntimeError("google-generativeai not installed") from e
        genai.configure(api_key=settings.google_api_key)
        self.model = genai.GenerativeModel(
            model_name=settings.gemini_model,
            system_instruction=SYSTEM_INSTRUCTION,
        )

    def classify_batch(self, items: list[dict]) -> list[ClassificationResult]:
        user_msg = _format_user_message(items)
        last_err: Exception | None = None
        t0 = time.time()
        for attempt in range(3):
            try:
                resp = self.model.generate_content(
                    user_msg,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 0.1,
                        "max_output_tokens": 6000,
                    },
                )
                data = json.loads(resp.text)
                log.info("gemini.batch ok", extra={"count": len(data),
                                                    "ms": int((time.time() - t0) * 1000),
                                                    "model": settings.gemini_model})
                return [_parse_classification(d) for d in data]
            except Exception as e:
                last_err = e
                log.warning(f"gemini.batch attempt={attempt} failed: {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Gemini failed after 3 attempts: {last_err}")

    def reevaluate(self, item: dict) -> ClassificationResult:
        results = self.classify_batch([item])
        return results[0] if results else ClassificationResult(
            id=str(item.get("id", "")), cth="", reasoning="No response", confidence=0.0,
        )

    def chat_stream(self, question: str, context_items: list[dict]) -> Iterator[str]:
        ctx = _build_chat_context(context_items)
        user_msg = f"Question: {question}\n\nCTH context:\n{ctx}\n\nAnswer concisely."
        try:
            stream = self.model.generate_content(
                user_msg,
                generation_config={"temperature": 0.2},
                stream=True,
            )
            for chunk in stream:
                txt = getattr(chunk, "text", "") or ""
                if txt:
                    yield txt
        except Exception as e:
            log.warning(f"gemini.chat_stream failed: {e}")
            yield f"(Gemini error: {e})"


class HeuristicClassifier:
    """Offline fallback: pick the top retrieval candidate. No LLM call."""
    def classify_batch(self, items: list[dict]) -> list[ClassificationResult]:
        out: list[ClassificationResult] = []
        for it in items:
            # Prefer candidates NOT ruled out by Stage-2 hard exclusions.
            cands = [c for c in it.get("candidates", []) if not c.get("legal_pruned")]
            if cands:
                top = cands[0]
                bcd = top.get("bcd_effective")
                if bcd is None:
                    bcd = top.get("bcd_statutory")
                # Conformal set = candidates within the retrieval margin of the top.
                top_score = float(top.get("retrieval_score", 0.0))
                conformal = [
                    c["cth"] for c in cands[:4]
                    if top_score - float(c.get("retrieval_score", 0.0)) <= 0.08
                ] or [top["cth"]]
                out.append(ClassificationResult(
                    id=str(it["id"]),
                    cth=top["cth"],
                    bcd_rate=float(bcd) if bcd is not None else None,
                    reasoning=f"Heuristic (no LLM key): top legally-surviving candidate (score={top_score:.3f}).",
                    confidence=min(0.85, max(0.5, top_score)),
                    conformal_set=conformal,
                    gir_path="heuristic: top lexical candidate",
                ))
            else:
                out.append(ClassificationResult(
                    id=str(it["id"]), cth="", bcd_rate=None,
                    reasoning="No candidates", confidence=0.0,
                ))
        return out

    def reevaluate(self, item: dict) -> ClassificationResult:
        return self.classify_batch([item])[0]

    def chat_stream(self, question: str, context_items: list[dict]) -> Iterator[str]:
        if not context_items:
            yield "No matching CTH candidates found in the tariff master for that query."
            return
        yield "Closest matches in the tariff master:\n\n"
        for c in context_items[:5]:
            rate = c.get("bcd_effective") if c.get("bcd_effective") is not None else c.get("bcd_statutory")
            rate_s = f"{rate}%" if rate is not None else "—"
            desc = (c.get("description") or "")[:200]
            yield f"• {c['cth']} — {desc} (BCD {rate_s})\n"
        yield "\n(Heuristic mode — no LLM key configured.)"


def get_classifier() -> LLMClassifier:
    """Pick provider per LLM_PROVIDER env, with graceful fallback."""
    provider = settings.llm_provider.lower()
    if provider == "claude" and settings.anthropic_api_key:
        try:
            return ClaudeClassifier()
        except Exception as e:
            print(f"[llm] Claude unavailable: {e}; falling back")
    if provider == "gemini" and settings.google_api_key:
        try:
            return GeminiClassifier()
        except Exception as e:
            print(f"[llm] Gemini unavailable: {e}; falling back")
    if settings.anthropic_api_key:
        try:
            return ClaudeClassifier()
        except Exception:
            pass
    if settings.google_api_key:
        try:
            return GeminiClassifier()
        except Exception:
            pass
    print("[llm] No API keys set; using HeuristicClassifier (top retrieval candidate)")
    return HeuristicClassifier()
