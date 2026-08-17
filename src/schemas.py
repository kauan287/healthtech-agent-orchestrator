from pydantic import BaseModel, Field
from typing import List

class TriageRecommendation(BaseModel):
    proposed_medication: str = Field(
        description="Nombre del medicamento recomendado o principio activo."
    )
    dosage: str = Field(
        description="Dosis y frecuencia recomendada (ej. '500mg cada 8 horas')."
    )
    reasoning: str = Field(
        description="Justificación clínica breve de la propuesta."
    )
    urgency_level: str = Field(
        description="Nivel de urgencia: ALTO, MEDIO o BAJO."
    )

class SafetyCheck(BaseModel):
    is_safe: bool = Field(
        description="True si el medicamento es seguro y no genera conflicto con las alergias. False en caso contrario."
    )
    risk_notes: str = Field(
        description="Explicación detallada de la evaluación de seguridad y posibles riesgos o contraindicaciones."
    )