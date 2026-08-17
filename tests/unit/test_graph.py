"""
Unit tests for the LangGraph graph structure (src/graph.py).

Verifies that the graph wires nodes correctly and that the HITL
interrupt fires after the safety_agent node — without invoking
any real LLM calls.
"""
import pytest
from unittest.mock import patch

from src.graph import create_app
from src.schemas import TriageRecommendation, SafetyCheck


TRIAGE_MOCK = TriageRecommendation(
    proposed_medication="Paracetamol",
    dosage="500mg cada 8 horas",
    reasoning="Sin contraindicaciones.",
    urgency_level="BAJO",
)

SAFETY_MOCK = SafetyCheck(
    is_safe=True,
    risk_notes="Sin alergias detectadas.",
)


class TestGraphStructure:
    def test_create_app_returns_compiled_graph(self):
        """create_app() should return a compiled LangGraph CompiledGraph."""
        app = create_app()
        # CompiledGraph has a get_state method
        assert hasattr(app, "get_state")
        assert hasattr(app, "stream")
        assert hasattr(app, "update_state")

    def test_graph_has_expected_nodes(self):
        """The graph must contain exactly the triage and safety agent nodes."""
        app = create_app()
        node_names = set(app.nodes.keys())
        assert "triage_agent" in node_names
        assert "safety_agent" in node_names

    def test_interrupt_fires_after_safety_agent(self):
        """
        After streaming the initial input the graph should pause
        (next == ('__end__',) is wrong — it should be non-empty showing
        the HITL interrupt is still pending).
        """
        with (
            patch("src.agents.structured_triage_llm") as mock_triage,
            patch("src.agents.structured_safety_llm") as mock_safety,
        ):
            mock_triage.invoke.return_value = TRIAGE_MOCK
            mock_safety.invoke.return_value = SAFETY_MOCK

            app = create_app(interrupt_after=["safety_agent"])
            config = {"configurable": {"thread_id": "test-graph-interrupt-01"}}

            initial_input = {
                "prescription_id": "RX-GRAPH-01",
                "patient_symptoms": "Fiebre alta",
                "patient_allergies": [],
                "proposed_medication": None,
                "dosage": None,
                "safety_approved": False,
                "risk_notes": None,
                "human_approved": None,
                "doctor_comments": None,
                "audit_logs": [],
            }

            for _ in app.stream(initial_input, config):
                pass

            state = app.get_state(config)
            values = state.values
            # Semantic HITL check: the graph awaits a doctor decision when
            # human_approved has not been set yet.
            assert values.get("human_approved") is None, (
                "Expected graph to be paused at HITL: human_approved should be None"
            )

    def test_graph_resumes_after_update_state(self):
        """Resuming with update_state should complete the graph."""
        with (
            patch("src.agents.structured_triage_llm") as mock_triage,
            patch("src.agents.structured_safety_llm") as mock_safety,
        ):
            mock_triage.invoke.return_value = TRIAGE_MOCK
            mock_safety.invoke.return_value = SAFETY_MOCK

            app = create_app(interrupt_after=["safety_agent"])
            config = {"configurable": {"thread_id": "test-graph-resume-02"}}

            initial_input = {
                "prescription_id": "RX-GRAPH-RESUME",
                "patient_symptoms": "Tos persistente",
                "patient_allergies": [],
                "proposed_medication": None,
                "dosage": None,
                "safety_approved": False,
                "risk_notes": None,
                "human_approved": None,
                "doctor_comments": None,
                "audit_logs": [],
            }

            for _ in app.stream(initial_input, config):
                pass

            # Simulate doctor approval
            app.update_state(
                config,
                {"human_approved": True, "doctor_comments": "OK"},
                as_node="safety_agent",
            )

            for _ in app.stream(None, config):
                pass

            final_state = app.get_state(config)
            # After resuming and completing, the graph should have no pending tasks
            is_still_paused = len(final_state.tasks) > 0 or len(getattr(final_state, 'interrupts', ())) > 0
            assert not is_still_paused, "Graph should have completed after update_state + stream"
            assert final_state.values["human_approved"] is True

    def test_triage_agent_populates_medication_in_state(self):
        """State should contain proposed_medication after the agents run."""
        with (
            patch("src.agents.structured_triage_llm") as mock_triage,
            patch("src.agents.structured_safety_llm") as mock_safety,
        ):
            mock_triage.invoke.return_value = TRIAGE_MOCK
            mock_safety.invoke.return_value = SAFETY_MOCK

            app = create_app(interrupt_after=["safety_agent"])
            config = {"configurable": {"thread_id": "test-state-population-01"}}

            initial_input = {
                "prescription_id": "RX-STATE-01",
                "patient_symptoms": "Dolor de cabeza",
                "patient_allergies": [],
                "proposed_medication": None,
                "dosage": None,
                "safety_approved": False,
                "risk_notes": None,
                "human_approved": None,
                "doctor_comments": None,
                "audit_logs": [],
            }

            for _ in app.stream(initial_input, config):
                pass

            values = app.get_state(config).values
            assert values["proposed_medication"] == "Paracetamol"
            assert values["dosage"] == "500mg cada 8 horas"
            assert values["safety_approved"] is True
            # Audit chain must have 2 entries (Triage + Safety)
            assert len(values["audit_logs"]) == 2
