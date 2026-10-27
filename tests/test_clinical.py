import pytest
from pydantic import ValidationError

from src.clinical import DemoClinicalModel
from tests.conftest import make_settings

model = DemoClinicalModel()


@pytest.mark.parametrize(
    ("symptoms", "medication"),
    [
        ("Fiebre de 39 °C y dolor articular", "Paracetamol"),
        ("TOS seca desde hace tres días", "Dextrometorfano"),
        ("Estornudos y picor de ojos", "Loratadina"),
        ("Cansancio general", "Paracetamol"),
    ],
)
def test_demo_triage_picks_protocol_by_symptom(symptoms: str, medication: str) -> None:
    assert model.triage(symptoms, []).proposed_medication == medication


def test_demo_safety_flags_allergy_ignoring_case_and_accents() -> None:
    check = model.check_safety("Paracetamol", "500 mg", "fiebre", ["Polen", "ACETAMINOFÉN"])
    assert check.is_safe is False
    assert check.risk_notes == "Contraindicado: alergia a ACETAMINOFÉN."


def test_demo_safety_passes_unrelated_allergy() -> None:
    assert model.check_safety("Paracetamol", "500 mg", "fiebre", ["Ibuprofeno"]).is_safe is True


def test_groq_provider_without_key_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError, match="requires GROQ_API_KEY"):
        make_settings(LLM_PROVIDER="groq", GROQ_API_KEY="")


def test_production_refuses_a_short_signing_key() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        make_settings(LLM_PROVIDER="demo", AUDIT_SIGNING_KEY="short")


def test_production_is_the_default_and_refuses_the_development_signing_key() -> None:
    with pytest.raises(ValidationError, match="AUDIT_SIGNING_KEY"):
        make_settings(LLM_PROVIDER="demo")
