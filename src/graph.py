"""LangGraph agent pipeline: the triage agent proposes, then the safety agent checks.

The graph has no checkpointer. A paused prescription is two signed audit entries
and a PENDING_APPROVAL row in the database, so the doctor's decision does not
need to resume a graph run. See "Design decisions" in the README.
"""

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.audit import AuditEntry, new_entry
from src.clinical import ClinicalModel, TriageRecommendation
from src.prescription import Step


class PipelineState(TypedDict, total=False):
    prescription_id: str
    patient_symptoms: str
    patient_allergies: list[str]
    triage: TriageRecommendation
    audit_logs: Annotated[list[AuditEntry], operator.add]


Pipeline = CompiledStateGraph[PipelineState, None, PipelineState, PipelineState]


def build_pipeline(model: ClinicalModel, signing_key: bytes) -> Pipeline:
    def audit(state: PipelineState, step: Step, details: dict[str, Any]) -> AuditEntry:
        return new_entry(
            state.get("audit_logs", []),
            key=signing_key,
            prescription_id=state["prescription_id"],
            step=step.value,
            details=details,
        )

    def triage_agent(state: PipelineState) -> PipelineState:
        triage = model.triage(state["patient_symptoms"], state["patient_allergies"])
        details = {
            "patient_symptoms": state["patient_symptoms"],
            "patient_allergies": state["patient_allergies"],
            **triage.model_dump(),
        }
        return {"triage": triage, "audit_logs": [audit(state, Step.TRIAGE, details)]}

    def safety_agent(state: PipelineState) -> PipelineState:
        triage = state["triage"]
        check = model.check_safety(
            triage.proposed_medication,
            triage.dosage,
            state["patient_symptoms"],
            state["patient_allergies"],
        )
        return {"audit_logs": [audit(state, Step.SAFETY, check.model_dump())]}

    builder = StateGraph(PipelineState)
    builder.add_node("triage_agent", triage_agent)
    builder.add_node("safety_agent", safety_agent)
    builder.add_edge(START, "triage_agent")
    builder.add_edge("triage_agent", "safety_agent")
    builder.add_edge("safety_agent", END)
    return builder.compile()
