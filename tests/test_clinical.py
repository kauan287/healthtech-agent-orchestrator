import json
from typing import Any

import pytest
from pydantic import ValidationError

from src.clinical import DemoClinicalModel, GroqClinicalModel
from tests.conftest import make_settings
from tests.fake_groq import Reply, completion, fake_groq

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


def _triage_reply(body: dict[str, Any]) -> Reply:
    proposal = {
        "proposed_medication": "Ibuprofeno",
        "dosage": "400 mg cada 8 horas",
        "reasoning": "AINE para el dolor.",
        "urgency_level": "BAJO",
    }
    if "response_format" in body:
        return completion(json.dumps(proposal), input_tokens=120, output_tokens=30)
    status, reply, headers = completion("", input_tokens=120, output_tokens=30)
    reply["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "TriageRecommendation", "arguments": json.dumps(proposal)},
        }
    ]
    reply["choices"][0]["finish_reason"] = "tool_calls"
    return status, reply, headers


def test_groq_gpt_oss_asks_for_strict_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    with fake_groq(_triage_reply) as groq:
        monkeypatch.setenv("GROQ_API_BASE", groq.url)
        proposal = GroqClinicalModel("test-key", "openai/gpt-oss-120b").triage("dolor", [])

    assert proposal.proposed_medication == "Ibuprofeno"
    [request] = groq.requests
    assert request["model"] == "openai/gpt-oss-120b"
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["name"] == "TriageRecommendation"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert "tools" not in request


def test_groq_other_models_use_tool_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    with fake_groq(_triage_reply) as groq:
        monkeypatch.setenv("GROQ_API_BASE", groq.url)
        proposal = GroqClinicalModel("test-key", "qwen/qwen3-32b").triage("dolor", [])

    assert proposal.urgency_level == "BAJO"
    [request] = groq.requests
    assert "response_format" not in request
    assert request["tool_choice"]["function"]["name"] == "TriageRecommendation"
