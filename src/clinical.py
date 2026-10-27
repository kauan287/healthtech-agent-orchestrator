"""Clinical decision models behind the triage and safety agents.

`GroqClinicalModel` asks an LLM for structured output. `DemoClinicalModel` is a
deterministic rule table so the API, tests, and Docker demo run without an API
key. Neither is medical advice. The doctor's sign-off is the control.
"""

import unicodedata
from typing import Literal, Protocol, cast

from pydantic import BaseModel, Field, SecretStr

from src.config import Settings


class TriageRecommendation(BaseModel):
    proposed_medication: str = Field(description="Nombre del medicamento o principio activo.")
    dosage: str = Field(description="Dosis y frecuencia, por ejemplo '500 mg cada 8 horas'.")
    reasoning: str = Field(description="Justificación clínica breve.")
    urgency_level: Literal["ALTO", "MEDIO", "BAJO"] = Field(description="Nivel de urgencia.")


class SafetyCheck(BaseModel):
    is_safe: bool = Field(
        description="False si el medicamento entra en conflicto con alguna alergia del paciente."
    )
    risk_notes: str = Field(description="Evaluación de seguridad, riesgos y contraindicaciones.")


class ClinicalModel(Protocol):
    def triage(self, symptoms: str, allergies: list[str]) -> TriageRecommendation: ...

    def check_safety(
        self, medication: str, dosage: str, symptoms: str, allergies: list[str]
    ) -> SafetyCheck: ...


def _allergy_list(allergies: list[str]) -> str:
    return ", ".join(allergies) if allergies else "Ninguna"


class GroqClinicalModel:
    def __init__(self, api_key: str, model: str) -> None:
        from langchain_groq import ChatGroq

        llm = ChatGroq(
            api_key=SecretStr(api_key), model=model, temperature=0, timeout=30, max_retries=2
        )
        self._triage_llm = llm.with_structured_output(TriageRecommendation)
        self._safety_llm = llm.with_structured_output(SafetyCheck)

    def triage(self, symptoms: str, allergies: list[str]) -> TriageRecommendation:
        prompt = f"""
        Eres un Agente Especialista en Triaje Médico.
        Analiza la siguiente información del paciente:
        - Síntomas: {symptoms}
        - Alergias conocidas: {_allergy_list(allergies)}

        Genera una propuesta inicial de tratamiento. Sé precavido.
        """
        return cast(TriageRecommendation, self._triage_llm.invoke(prompt))

    def check_safety(
        self, medication: str, dosage: str, symptoms: str, allergies: list[str]
    ) -> SafetyCheck:
        prompt = f"""
        Eres un Agente Farmacéutico de Seguridad Clínica.
        Evalúa si la siguiente propuesta médica es segura para el paciente:

        - Medicamento propuesto: {medication}
        - Dosis propuesta: {dosage}
        - Alergias del paciente: {_allergy_list(allergies)}
        - Síntomas: {symptoms}

        IMPORTANTE: si el medicamento contiene un principio activo relacionado con las alergias
        del paciente, DEBES marcar is_safe como False.
        """
        return cast(SafetyCheck, self._safety_llm.invoke(prompt))


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# First matching row wins.
DEMO_PROTOCOLS: list[tuple[tuple[str, ...], TriageRecommendation]] = [
    (
        ("fiebre", "fever", "dolor", "pain", "cabeza", "headache"),
        TriageRecommendation(
            proposed_medication="Paracetamol",
            dosage="500 mg cada 8 horas",
            reasoning="Analgésico y antipirético de primera línea.",
            urgency_level="MEDIO",
        ),
    ),
    (
        ("tos", "cough"),
        TriageRecommendation(
            proposed_medication="Dextrometorfano",
            dosage="15 mg cada 6 horas",
            reasoning="Antitusivo para tos seca no productiva.",
            urgency_level="BAJO",
        ),
    ),
    (
        ("estornud", "picor", "rinitis", "sneez", "itch"),
        TriageRecommendation(
            proposed_medication="Loratadina",
            dosage="10 mg una vez al día",
            reasoning="Antihistamínico no sedante para síntomas alérgicos.",
            urgency_level="BAJO",
        ),
    ),
]

DEMO_FALLBACK = TriageRecommendation(
    proposed_medication="Paracetamol",
    dosage="500 mg cada 8 horas",
    reasoning="Sin protocolo específico. Tratamiento sintomático y revisión médica.",
    urgency_level="BAJO",
)

# Allergy terms that contraindicate each medication, including drug families.
DEMO_CONTRAINDICATIONS: dict[str, set[str]] = {
    "paracetamol": {"paracetamol", "acetaminofen", "acetaminophen"},
    "dextrometorfano": {"dextrometorfano", "dextromethorphan", "opioides", "opioids"},
    "loratadina": {"loratadina", "loratadine", "antihistaminicos", "antihistamines"},
}


class DemoClinicalModel:
    def triage(self, symptoms: str, allergies: list[str]) -> TriageRecommendation:
        text = _normalize(symptoms)
        for keywords, recommendation in DEMO_PROTOCOLS:
            if any(_normalize(keyword) in text for keyword in keywords):
                return recommendation
        return DEMO_FALLBACK

    def check_safety(
        self, medication: str, dosage: str, symptoms: str, allergies: list[str]
    ) -> SafetyCheck:
        blocked_by = {
            _normalize(term) for term in DEMO_CONTRAINDICATIONS.get(_normalize(medication), ())
        }
        conflicts = [a for a in allergies if _normalize(a) in blocked_by]
        if conflicts:
            return SafetyCheck(
                is_safe=False,
                risk_notes=f"Contraindicado: alergia a {', '.join(conflicts)}.",
            )
        return SafetyCheck(is_safe=True, risk_notes="Sin conflictos con las alergias conocidas.")


def build_clinical_model(settings: Settings) -> ClinicalModel:
    if settings.LLM_PROVIDER == "demo":
        return DemoClinicalModel()
    return GroqClinicalModel(api_key=settings.GROQ_API_KEY, model=settings.GROQ_MODEL)
