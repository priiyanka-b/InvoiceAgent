import os

from langchain_core.messages import SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from tools import TOOLS

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

PROMPT = SystemMessage(
    "You work in accounts payable. You are given an invoice that failed automatic "
    "matching. Look up the invoice, its purchase order and the vendor, read the "
    "policy, then either resolve the invoice or escalate it. Base every decision on "
    "the policy and quote the numbers you compared. If a reviewer declines your "
    "action, escalate instead of retrying. Finish with a two line summary."
)


def build_graph(checkpointer, model=None):
    model = (model or ChatGoogleGenerativeAI(model=MODEL, temperature=0)).bind_tools(TOOLS)

    def call_model(state: MessagesState):
        return {"messages": [model.invoke([PROMPT] + state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", call_model)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    # the checkpointer is what lets a run stop at the approval step and resume later,
    # even after a server restart
    return g.compile(checkpointer=checkpointer)
