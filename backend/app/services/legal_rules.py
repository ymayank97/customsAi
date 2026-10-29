"""Optional expert-reviewed exclusions. A conflict always requests review.

No legal rules are inferred from keywords by an LLM. An administrator supplies
rules.json for the tariff version: [{id, citation, all_tokens, excluded_prefixes}].
This is a guard, not a complete implementation of tariff interpretation rules.
"""
import json
from functools import lru_cache

from backend.app.config import settings
from backend.app.core.text_cleaning import token_set_for_overlap


@lru_cache(maxsize=1)
def load_rules():
    path = settings.data_dir / "rules.json"
    if not path.exists():
        return []
    rules = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rules, list):
        raise ValueError("rules.json must be a list of reviewed rules")
    for rule in rules:
        if not all(rule.get(key) for key in ("id", "citation", "all_tokens", "excluded_prefixes")):
            raise ValueError("Each reviewed rule needs id, citation, all_tokens and excluded_prefixes")
        if any(not isinstance(prefix, str) or len(prefix) not in (2, 4, 6, 8) or not prefix.isdigit()
               for prefix in rule["excluded_prefixes"]):
            raise ValueError("Rule prefixes must be 2, 4, 6 or 8 digit strings")
    return rules


def conflicts(description, code):
    if not code:
        return []
    tokens = token_set_for_overlap(description)
    return [rule for rule in load_rules()
            if set(rule["all_tokens"]).issubset(tokens)
            and any(code.startswith(prefix) for prefix in rule["excluded_prefixes"])]
