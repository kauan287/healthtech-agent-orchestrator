"""Reference allergy rule: the ground truth the agents are scored against.

An allergy term that names a family, an alias, or any member blocks the whole family.
A medication conflicts when a blocked ingredient appears in it as whole words.
"""

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

FAMILIES_PATH = Path(__file__).with_name("allergy_families.json")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.split())


def contains_words(text: str, term: str) -> bool:
    """True when `term` appears in `text` bounded by non-alphanumerics, after normalising both."""
    pattern = rf"(?<![a-z0-9]){re.escape(normalize(term))}(?![a-z0-9])"
    return re.search(pattern, normalize(text)) is not None


@dataclass(frozen=True)
class AllergyTable:
    families: dict[str, frozenset[str]]
    aliases: dict[str, str]

    def families_named_in(self, allergy: str) -> set[str]:
        named = {f for f in self.families if contains_words(allergy, f)}
        named |= {f for alias, f in self.aliases.items() if contains_words(allergy, alias)}
        named |= {
            f
            for f, members in self.families.items()
            if any(contains_words(allergy, m) for m in members)
        }
        return named


@cache
def load_table(path: Path = FAMILIES_PATH) -> AllergyTable:
    raw = json.loads(path.read_text(encoding="utf-8"))
    families = {normalize(f): frozenset(map(normalize, m)) for f, m in raw["families"].items()}
    aliases = {normalize(a): normalize(f) for a, f in raw["aliases"].items()}
    return AllergyTable(families=families, aliases=aliases)


def blocked_ingredients(allergies: Iterable[str]) -> set[str]:
    """Every ingredient the allergies rule out. A term outside the table blocks only itself."""
    table = load_table()
    blocked: set[str] = set()
    for allergy in allergies:
        families = table.families_named_in(allergy)
        if not families:
            blocked.add(normalize(allergy))
        for family in families:
            blocked |= table.families[family]
    return blocked


def matches_any(medication: str, ingredients: Iterable[str]) -> bool:
    return any(contains_words(medication, ingredient) for ingredient in ingredients)


def conflicts(medication: str, allergies: Iterable[str]) -> bool:
    return matches_any(medication, blocked_ingredients(allergies))
