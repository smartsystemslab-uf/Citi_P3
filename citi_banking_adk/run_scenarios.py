import argparse
import asyncio
import json
import os

from dotenv import load_dotenv
from bank_adk.runner import run_many
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SCENARIOS = {
    "front_office_monitoring": "Review transaction TXN-1001 for customer CUST-001. Run the front-office transaction monitoring, fraud, and compliance workflow and decide whether to approve or send to manual review.",
    "front_office_chatbot": "Customer asks: How do I dispute a debit card transaction and what is the expected timeline?",
    "front_office_transaction_execution": "Customer service request: Transfer 125.50 USD from source account CHK-002 to destination account EXT-998 for customer CUST-002 with memo Utility backup payment.",
    "mid_office_planning": "Use the mid-office planning workflow to analyze operations data for 2026-04-21, forecast workload, propose staffing, validate it, and provide support guidance.",
    "mid_office_rep_assist": "Representative assist request for customer CUST-001. Provide customer data, a product suggestion for idle cash, loan guidance for a home equity inquiry, and policy/risk checks.",
    "back_office_clean": "Core banking system event: Run end-of-day processing for batch EOD-2026-04-21-CLEAN and complete reconciliation and reporting.",
    "back_office_mismatch": "Core banking system event: Run end-of-day processing for batch EOD-2026-04-21-MISMATCH and handle any reconciliation mismatch according to the workflow.",
}


def _ser(obj):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return str(obj)


async def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', choices=[*SCENARIOS.keys(), 'all'], default='all')
    ap.add_argument('--raw', action='store_true', help='Print the full raw debug trace.')
    args = ap.parse_args()
    selected = SCENARIOS if args.scenario == 'all' else {args.scenario: SCENARIOS[args.scenario]}
    
    # 1. Define how to launch your MCP server as a subprocess
    # This replaces the URL logic.
    # TODO: Implement different launch logic based on scenario type (front/mid/back) if needed, e.g. different server scripts or env vars.
    '''mode_map = {
        "front": "front",
        "mid": "mid",
        "back": "back",
    }

    current_mode = "all"
    for key in mode_map:
        if args.scenario.startswith(key):
            current_mode = mode_map[key]
            '''

    server_params = StdioServerParameters(
        command="python",
        args=["../citi_banking_mcp_server/mcp_server.py"],  # Ensure this filename is exactly correct
        env=os.environ.copy()
    )

    print(f"🔌 Spawning MCP Server via Stdio...")

    try:
        # 2. Launch the server and establish the pipe
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # 3. Initialize the session protocol
                await session.initialize()
                
                tools_response = await session.list_tools()
                tool_names = [t.name for t in tools_response.tools]
                print(f"✅ Connected! Available MCP Tools: {tool_names}\n")

                # 4. Execute the scenarios
                results = await run_many(
                    list(selected.values()), 
                    mcp_session=session, 
                    compact=not args.raw
                )
                
                print(json.dumps({name: _ser(res) for name, res in zip(selected.keys(), results)}, indent=2, ensure_ascii=False))

    except Exception as e:
        print(f"❌ Failed to connect to MCP Server: {e}")
        print("Tip: Ensure you are in the directory where mcp_server.py is located.")

if __name__ == '__main__':
    asyncio.run(main())
