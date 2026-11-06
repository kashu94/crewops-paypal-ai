"""CrewPay as an MCP server: the agent's tools, with the same roles, for any MCP client.

    uv run python -m crewpay.mcp_server                        # viewer: read-only
    uv run python -m crewpay.mcp_server --role controller --user ananya.iyer
    uv run python -m crewpay.mcp_server --role crew --crew-id C-1111

The role is fixed when the server starts, so a client can only list and call the tools that
role may use. Writes still go through the engine's legality check and the trip's version.
The operation is the demo data, held in memory.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import anyio
from mcp import stdio_server, types
from mcp.server.lowlevel import Server

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.agent.tools import Roster
from crewpay.ops import CalloutDesk, StoreOutbox
from crewpay.ops.reports import ReportDesk
from crewpay.pay import PayConfig, PayPalClient, Payroll
from crewpay.settings import settings
from crewpay.store import open_store

INSTRUCTIONS = (
    "Crew control for three regional airlines based in Hyderabad. Every answer about legality, "
    "hours or cost must come from these tools: quote their numbers, never estimate. Times are "
    "IST. Turn names into crew ids with search_crew first. Approved cover is paid through "
    "PayPal Payouts: crew_payments shows each payment, its rupee lines and its PayPal status."
)


def build_server(
    store: Roster,
    caller: Caller,
    toolbox: ToolBox | None = None,
    now: datetime | None = None,
    desk: CalloutDesk | None = None,
    reports: ReportDesk | None = None,
    payroll: Payroll | None = None,
) -> Server:
    box = toolbox or ToolBox()
    ctx = Context(store, caller, now, desk, reports=reports, payroll=payroll)

    async def list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.parameters(),
                    annotations=types.ToolAnnotations(
                        read_only_hint=not tool.writes, destructive_hint=tool.writes
                    ),
                )
                for tool in box.available(caller, agent=True)
            ]
        )

    async def call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        result = await anyio.to_thread.run_sync(
            lambda: box.call(params.name, params.arguments or {}, ctx, agent=True)
        )
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(result, ensure_ascii=False))],
            structured_content=result,
            is_error="error" in result,
        )

    return Server(
        "crewpay",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=[r.value for r in Role], default=Role.AUDITOR.value)
    parser.add_argument("--user", default="mcp", help="recorded as who made a roster change")
    parser.add_argument("--crew-id", help="for --role crew: whose record this is")
    args = parser.parse_args()

    caller = Caller(args.user, Role(args.role), args.crew_id)
    config = settings()
    store = open_store(config)
    outbox = StoreOutbox(store)
    desk = CalloutDesk(store, outbox, docs=store, four_eyes=True)
    payroll = Payroll(
        store,
        outbox,
        PayPalClient.from_settings(config),
        PayConfig(
            currency=config.paypal_currency,
            fx_inr_per_usd=config.fx_inr_per_usd,
            cap=config.pay_cap,
            receiver=config.sandbox_receiver,
        ),
    )
    if desk.plans is not None:
        desk.plans.listeners.append(payroll.on_plan)
        payroll.funding.attach(desk.plans)
    server = build_server(
        store,
        caller,
        desk=desk,
        reports=ReportDesk(store, outbox, config.seed_dir),
        payroll=payroll,
    )

    async def serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(serve)


if __name__ == "__main__":
    main()
