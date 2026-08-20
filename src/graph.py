from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.agents import safety_agent_node, triage_agent_node
from src.state import PrescriptionState


def create_app(interrupt_after=None):
    """Construye y devuelve la aplicación/flujo compilado.

    Args:
        interrupt_after: lista de nodos tras los cuales pausar (HITL).
    Returns:
        app compilada por `StateGraph.compile()`.
    """
    builder = StateGraph(PrescriptionState)
    builder.add_node("triage_agent", triage_agent_node)
    builder.add_node("safety_agent", safety_agent_node)

    builder.add_edge(START, "triage_agent")
    builder.add_edge("triage_agent", "safety_agent")
    builder.add_edge("safety_agent", END)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory, interrupt_after=interrupt_after or ["safety_agent"])
