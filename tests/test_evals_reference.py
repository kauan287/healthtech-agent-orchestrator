import pytest

from evals.records import SafetyProbe, TriageCase, load_cases
from evals.reference import blocked_ingredients, conflicts, matches_any


@pytest.mark.parametrize(
    ("medication", "allergies"),
    [
        ("Ibuprofeno", ["Ibuprofeno"]),
        ("Ibuprofeno", ["AINEs"]),
        ("Naproxeno", ["Ibuprofeno"]),
        ("Ibuprofeno", ["NSAIDs"]),
        ("Amoxicilina", ["penicillin"]),
        ("Amoxicilina/ácido clavulánico", ["Penicilinas"]),
        ("Cetirizina", ["Antihistamínicos"]),
        ("Ibuprofeno (Espidifen)", ["Dexketoprofeno"]),
        ("Amoxicilina", ["Penicilina (dato antiguo, ignorar)"]),
        ("Paracetamol", ["acetaminophen"]),
        ("ASPIRINA  500 mg", ["Loratadina", "Naproxeno"]),
    ],
)
def test_conflict(medication: str, allergies: list[str]) -> None:
    assert conflicts(medication, allergies) is True


@pytest.mark.parametrize(
    ("medication", "allergies"),
    [
        ("Paracetamol", []),
        ("Paracetamol", ["AINEs"]),
        ("Azitromicina", ["Penicilinas"]),
        ("Dextrometorfano", ["Ibuprofeno"]),
        ("Omeprazol", ["Ibuprofeno", "Amoxicilina"]),
    ],
)
def test_no_conflict(medication: str, allergies: list[str]) -> None:
    assert conflicts(medication, allergies) is False


def test_a_family_name_blocks_every_member() -> None:
    assert blocked_ingredients(["Opioides"]) == {
        "codeina",
        "tramadol",
        "morfina",
        "oxicodona",
        "fentanilo",
        "codeine",
        "morphine",
        "oxycodone",
    }


def test_an_allergy_outside_the_table_blocks_only_itself() -> None:
    assert blocked_ingredients(["Marisco "]) == {"marisco"}


def test_ingredients_match_whole_words_only() -> None:
    assert matches_any("Codeína 30 mg", {"codeina"}) is True
    assert matches_any("Tricodeinal 30 mg", {"codeina"}) is False
    assert matches_any("Paracetamol/codeína", {"codeina"}) is True
    assert matches_any("Acido acetilsalicilico 500 mg", {"acido acetilsalicilico"}) is True


def test_every_probe_label_agrees_with_the_reference_rule() -> None:
    disagreements = [
        c.id
        for c in load_cases()
        if isinstance(c, SafetyProbe)
        and conflicts(c.medication, c.allergies) != c.expected_conflict
    ]
    assert disagreements == []


def test_no_acceptable_medication_conflicts_with_the_case_allergies() -> None:
    clashes = [
        (c.id, medication)
        for c in load_cases()
        if isinstance(c, TriageCase)
        for medication in c.acceptable_medications or []
        if conflicts(medication, c.allergies)
    ]
    assert clashes == []


@pytest.mark.parametrize(
    ("medication", "acceptable"),
    [
        ("Azithromycin", ["azitromicina"]),
        ("Amoxicillin 500 mg", ["amoxicilina"]),
        ("Acetaminophen", ["paracetamol"]),
        ("Fluticasone propionate nasal spray", ["fluticasona"]),
        ("Ibuprofen", ["ibuprofeno"]),
    ],
)
def test_an_english_name_matches_its_spanish_ingredient(
    medication: str, acceptable: list[str]
) -> None:
    assert matches_any(medication, acceptable)


def test_a_synonym_does_not_match_a_different_drug() -> None:
    assert not matches_any("Azithromycin", ["claritromicina", "paracetamol"])
