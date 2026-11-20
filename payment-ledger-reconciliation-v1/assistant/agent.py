"""
The investigation agent, built as a LangGraph graph.

    START -> guardrail --(blocked)--> END
                 |
                 v
               agent  <-------+
                 |            |
      (wants a tool?) --yes-> tools   (read-only MCP tools + RAG search)
                 |
                 no
                 v
               check  -> END   (refuses if the answer names a status no tool returned)

Try it from the terminal (needs ANTHROPIC_API_KEY in .env):
    python assistant/agent.py "Why is PAY-000003 unmatched?"
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
REASON_CODES = [
    "MISSING_IN_LEDGER", "MISSING_IN_PROCESSOR", "AMOUNT_MISMATCH", "FEE_MISMATCH",
    "SPLIT_SUM_MISMATCH", "DUPLICATE_CANDIDATE", "AMBIGUOUS_MATCH",
]
WRITE_WORDS = re.compile(r"\b(update|delete|insert|drop|alter|mark .* reconciled|close the exception)\b", re.I)

SYSTEM_PROMPT = """You are a read-only assistant that helps finance analysts investigate payment-to-ledger reconciliation exceptions.

How to answer:
- Look facts up with the tools before answering. Never state a payment's status, amount, rule or reason code unless a tool returned it in this conversation.
- For "why" or "what should I do" questions, also call search_playbooks and base the explanation on the rule catalog or SOP it returns.
- Amounts in the data are integer cents; show them as dollars (18029 cents = $180.29).
- End with an "Evidence" line listing the record IDs and doc sections you used.
- You cannot change data, mark items reconciled or close exceptions. If asked to, say an authorized analyst must do it.
- If a tool returns NOT_FOUND, say so plainly instead of guessing."""


def guardrail(state: MessagesState):
    question = state["messages"][-1].content
    if not isinstance(question, str) or len(question) > 1000:
        return {"messages": [AIMessage("Please ask one question of up to 1,000 characters.")]}
    if WRITE_WORDS.search(question):
        return {"messages": [AIMessage(
            "I can only look things up and explain them. Changing records or closing exceptions "
            "needs an authorized analyst.")]}
    return {}


def after_guardrail(state: MessagesState) -> str:
    return END if isinstance(state["messages"][-1], AIMessage) else "agent"


def check(state: MessagesState):
    """Refuse an answer that mentions a reason code no tool actually returned."""
    answer = state["messages"][-1]
    tool_text = " ".join(str(m.content) for m in state["messages"] if isinstance(m, ToolMessage))
    unsupported = [c for c in REASON_CODES if c in answer.text and c not in tool_text]
    if unsupported:
        return {"messages": [AIMessage(
            "I couldn't verify that answer against the data, so I won't state it. "
            f"(Unsupported claim: {', '.join(unsupported)}.) Please ask about a specific record ID.")]}
    return {}


async def build_graph():
    client = MultiServerMCPClient({
        "reconciliation": {
            "command": sys.executable,
            "args": [str(ROOT / "assistant/mcp_server.py")],
            "transport": "stdio",
            # The tool server only gets the settings it needs (backend choice, Databricks
            # connection), never the Anthropic key.
            "env": {k: v for k, v in os.environ.items()
                    if k.startswith(("RECON_", "DATABRICKS_")) or k in ("PATH", "HOME")},
        }
    })
    tools = await client.get_tools()
    llm = ChatAnthropic(model=MODEL, max_tokens=8000).bind_tools(tools)

    async def agent(state: MessagesState):
        reply = await llm.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    g = StateGraph(MessagesState)
    g.add_node("guardrail", guardrail)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(tools))
    g.add_node("check", check)
    g.add_edge(START, "guardrail")
    g.add_conditional_edges("guardrail", after_guardrail, {"agent": "agent", END: END})
    g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: "check"})
    g.add_edge("tools", "agent")
    g.add_edge("check", END)
    return g.compile()


def langfuse_callbacks() -> list:
    """Trace every run in Langfuse when its keys are set in .env; otherwise do nothing."""
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        from langfuse.langchain import CallbackHandler
        return [CallbackHandler()]
    return []


async def ask(graph, question: str) -> dict:
    result = await graph.ainvoke(
        {"messages": [("user", question)]},
        config={"callbacks": langfuse_callbacks()},
    )
    tools_used = [m.name for m in result["messages"] if isinstance(m, ToolMessage)]
    return {"answer": result["messages"][-1].text, "tools_used": tools_used}


async def _main(question: str) -> None:
    graph = await build_graph()
    out = await ask(graph, question)
    print(out["answer"])
    print("\nTools used:", ", ".join(out["tools_used"]) or "none")


if __name__ == "__main__":
    asyncio.run(_main(" ".join(sys.argv[1:]) or "Why is PAY-000003 unmatched?"))
