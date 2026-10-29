"""Conservative shared preprocessing. No stemming, NLP models or downloads."""
from __future__ import annotations

import re
import unicodedata

PREPROCESSING_VERSION = 1
_TARIFF = re.compile(r"\b(?:hsn|cth|hs\s+code)\s*[:#-]?\s*\d{2}(?:[ .-]?\d{2}){1,3}\b", re.I)
_REFERENCE = re.compile(r"\b(?:part\s+(?:no\.?|number)|model\s+(?:no\.?|number)|sku|invoice\s+no\.?)\s*[:#-]?\s*[\w/-]+", re.I)
_QUANTITY = re.compile(r"\b(?:qty|quantity)\s*[:=-]?\s*\d+(?:\.\d+)?\s*(?:pcs|pieces|nos|units)?\b|\b\d+\s*(?:pcs|pieces|nos)\b", re.I)
_BOILERPLATE = re.compile(r"\bas\s+per\s+(?:invoice|packing\s+list)\b", re.I)
_ABBREVIATIONS = {"assy": "assembly", "conn": "connector", "conns": "connectors"}


def clean_description(text: str | None) -> str:
    """Preserve negation, use, grades, voltage, dimensions and percentages.

    Remove only labelled references and shipment counts. Unlabelled numbers
    stay: guessing whether a number is a model or specification loses data.
    Training and inference use this exact same function.
    """
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    value = re.sub(r"[\u200b-\u200d\ufeff\u00ad]", "", value)
    for pattern in (_TARIFF, _REFERENCE, _QUANTITY, _BOILERPLATE):
        value = pattern.sub(" ", value)
    value = re.sub(r"[^\w\s.%/+\-]", " ", value)
    value = re.sub(r"(?<=\d)\s+(?=v\b|kv\b|w\b|kw\b|mm\b|cm\b|kg\b|hz\b|%)", "", value)
    value = re.sub(r"\b(?:assy|conn|conns)\b", lambda m: _ABBREVIATIONS[m[0]], value)
    return " ".join(value.split()).strip(" .-/+")


def normalize_for_matching(text: str | None) -> str:
    return clean_description(text)


def token_set_for_overlap(text: str | None) -> set[str]:
    return set(re.findall(r"\w+", clean_description(text)))
