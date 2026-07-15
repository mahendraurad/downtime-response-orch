"""
tests/test_inventory_tool.py
Smoke test: proves that AzureChatOpenAI can discover and call
check_inventory_tool via LangGraph tool-calling.

Run from the project root (venv active):
    python tests/test_inventory_tool.py
"""
import sys
from pathlib import Path
# Ensure the project root is on sys.path so 'tools' is importable
# regardless of which directory the script is launched from.
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import json

from dotenv import load_dotenv
 
load_dotenv()

from langchain_openai import AzureChatOpenAI
from tools.langgraph_tools import check_inventory_tool

# ---------------------------------------------------------------------------
# Build the LLM — same four .env variables as rationale_writer.py
# ---------------------------------------------------------------------------
llm = AzureChatOpenAI(
    openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    temperature=0.2,
    max_tokens=400,
)

# Bind the tool so the LLM knows it exists and can choose to call it
llm_with_tools = llm.bind_tools([check_inventory_tool])

# ---------------------------------------------------------------------------
# Ask the LLM — it should decide to call check_inventory_tool
# ---------------------------------------------------------------------------
print("=" * 60)
print("Sending prompt to LLM...")
print("=" * 60)

result = llm_with_tools.invoke(
    "Check if a bearing replacement part is available for bearing BRG_005"
)

# (a) Show what the LLM decided to call and with what arguments
print("\n(a) result.tool_calls  — did the LLM choose the tool?")
print("-" * 60)
if result.tool_calls:
    for tc in result.tool_calls:
        print(f"  Tool name : {tc['name']}")
        print(f"  Arguments : {tc['args']}")
else:
    print("  (no tool calls — LLM answered directly)")
    print(f"  Content: {result.content}")

# (b) Actually execute the tool with the arguments the LLM provided
print("\n(b) Real inventory answer from check_inventory_tool")
print("-" * 60)
if result.tool_calls:
    for tc in result.tool_calls:
        args = tc["args"]
        raw = check_inventory_tool.invoke(args)
        # The tool returns a JSON string; parse it for pretty printing
        try:
            data = json.loads(raw)
            for key, value in data.items():
                print(f"  {key:<22}: {value}")
        except json.JSONDecodeError:
            print(raw)
else:
    print("  (no tool call to execute)")

print("\n" + "=" * 60)
print("Done.")
