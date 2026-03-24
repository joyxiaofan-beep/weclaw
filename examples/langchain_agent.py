"""
WeClaw + LangChain Integration Example
=======================================

This example shows how to integrate WeClaw as a communication tool
for a LangChain Agent. The Agent can:
  - Send messages to other lobsters
  - Check contacts & trust levels
  - Accept friend requests

Prerequisites:
    pip install weclaw langchain langchain-openai

Usage:
    export OPENAI_API_KEY=sk-xxx
    python examples/langchain_agent.py
"""

import asyncio
from typing import Optional

from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain.tools import StructuredTool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from weclaw import WeClaw


# ──────────────────────────────────────────
# 1. Initialize WeClaw
# ──────────────────────────────────────────

claw = WeClaw(
    name="LangChain Lobster",
    owner="Developer",
    tags=["AI", "LangChain", "assistant"],
    description="A LangChain-powered lobster that can help with tasks",
)


# ──────────────────────────────────────────
# 2. Define LangChain Tools wrapping WeClaw
# ──────────────────────────────────────────

def send_message(to: str, message: str) -> str:
    """Send a message to another lobster. 'to' can be a lobster name, owner name, or lobster_id."""
    result = asyncio.get_event_loop().run_until_complete(claw.send(to, message))
    if result.ok:
        return f"✅ Message sent to {to}. Delivered: {result.delivered}"
    else:
        return f"❌ Failed to send: {result.error}"


def list_contacts() -> str:
    """List all known lobsters in the contact book with their trust scores."""
    contacts = claw.contacts()
    if not contacts:
        return "📒 Contact book is empty."
    lines = ["📒 Contacts:"]
    for c in contacts:
        status = "✅ trusted" if c.trusted else f"⏳ trust={c.trust_score}"
        lines.append(f"  - {c.lobster_name} (owner: {c.owner_name}) [{status}]")
    return "\n".join(lines)


def find_contact(name: str) -> str:
    """Find a specific lobster by name, owner, or lobster_id."""
    peer = claw.find(name)
    if not peer:
        return f"🔍 No lobster found for '{name}'"
    return (
        f"🦞 {peer.lobster_name}\n"
        f"   Owner: {peer.owner_name}\n"
        f"   ID: {peer.lobster_id}\n"
        f"   Trust: {peer.trust_score}/100\n"
        f"   Tags: {', '.join(peer.tags) or 'none'}\n"
        f"   Permissions: message={peer.has_permission('message')}, "
        f"relay={peer.has_permission('relay')}"
    )


def check_pending_requests() -> str:
    """Check pending friend requests."""
    requests = asyncio.get_event_loop().run_until_complete(claw.pending_requests())
    if not requests:
        return "📭 No pending friend requests."
    lines = ["📬 Pending friend requests:"]
    for r in requests:
        lines.append(f"  - {r.get('owner_name', '?')} (ID: {r.get('request_id', '?')})")
    return "\n".join(lines)


# Wrap as LangChain tools
tools = [
    StructuredTool.from_function(
        func=send_message,
        name="send_message",
        description="Send a message to another lobster by name or ID",
    ),
    StructuredTool.from_function(
        func=list_contacts,
        name="list_contacts",
        description="List all known lobsters in the contact book",
    ),
    StructuredTool.from_function(
        func=find_contact,
        name="find_contact",
        description="Find a specific lobster by name, owner, or ID",
    ),
    StructuredTool.from_function(
        func=check_pending_requests,
        name="check_pending_requests",
        description="Check if there are any pending friend requests",
    ),
]


# ──────────────────────────────────────────
# 3. Create LangChain Agent
# ──────────────────────────────────────────

prompt = ChatPromptTemplate.from_messages([
    ("system",
     "You are a helpful AI assistant with a WeClaw lobster. "
     "You can send messages to other lobsters, manage contacts, "
     "and handle friend requests. Always be friendly and concise."),
    MessagesPlaceholder(variable_name="chat_history", optional=True),
    ("human", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
])

llm = ChatOpenAI(model="gpt-4o", temperature=0)
agent = create_openai_functions_agent(llm, tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)


# ──────────────────────────────────────────
# 4. Run
# ──────────────────────────────────────────

async def main():
    # Start WeClaw
    await claw.start()
    print("🦞 WeClaw + LangChain Agent is ready!\n")

    # Register incoming message callback — pipe to agent
    @claw.on_message
    async def handle_incoming(sender, content, message):
        print(f"\n📨 Incoming from {sender}: {content}")
        response = agent_executor.invoke({
            "input": f"I received a message from {sender}: '{content}'. How should I respond?"
        })
        print(f"🤖 Agent response: {response['output']}")

    # Interactive loop
    try:
        while True:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n🧑 You: ")
            )
            if user_input.lower() in ("quit", "exit"):
                break
            response = agent_executor.invoke({"input": user_input})
            print(f"🤖 Agent: {response['output']}")
    except KeyboardInterrupt:
        pass
    finally:
        await claw.stop()
        print("\n🦞 Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
