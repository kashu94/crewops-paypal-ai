import pytest
from conftest import SEED_DIR
from mcp import Client

from crewpay.agent import Caller, Role
from crewpay.mcp_server import build_server
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

LAPSED = "C-1111"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.mark.anyio
async def test_a_viewer_lists_read_only_tools(mem):
    async with Client(build_server(mem, Caller("ops", Role.AUDITOR))) as client:
        tools = (await client.list_tools()).tools
    names = {t.name for t in tools}
    assert "assign_crew" not in names and {"find_cover", "check_legality"} <= names
    assert all(t.annotations.read_only_hint for t in tools)
    assert all(t.input_schema["type"] == "object" for t in tools)


@pytest.mark.anyio
async def test_tools_return_the_engine_result_as_structured_content(mem):
    async with Client(build_server(mem, Caller("ops", Role.AUDITOR))) as client:
        result = await client.call_tool("check_legality", {"crew_id": LAPSED, "trip_id": "T-1018"})
    assert not result.is_error
    assert result.structured_content["legal"] is False
    assert result.structured_content["blockers"][0]["rule"] == "FDTL-08"
    assert '"legal": false' in result.content[0].text


@pytest.mark.anyio
async def test_a_controller_can_change_the_roster_and_a_viewer_cannot(mem):
    args = {
        "trip_id": "T-1018",
        "replace_crew_id": LAPSED,
        "with_crew_id": "C-1157",
        "expected_version": 1,
        "reason": "medical lapses",
    }
    async with Client(build_server(mem, Caller("ops", Role.AUDITOR))) as client:
        refused = await client.call_tool("assign_crew", args)
    assert refused.is_error and "cannot use" in refused.structured_content["error"]

    async with Client(build_server(mem, Caller("ananya.iyer", Role.CONTROLLER))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        done = await client.call_tool("assign_crew", args)
    assert tools["assign_crew"].annotations.destructive_hint
    assert not done.is_error and done.structured_content["version"] == 2
    assert [c.actor for c in mem.changes()] == ["ananya.iyer"]


@pytest.mark.anyio
async def test_a_crew_member_sees_only_their_own_tools_and_record(mem):
    me = Caller("pooja", Role.CREW, LAPSED)
    async with Client(build_server(mem, me)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        other = await client.call_tool("crew_profile", {"crew_id": "C-1157"})
    assert names == {
        "find_trips",
        "trip_details",
        "crew_profile",
        "check_legality",
        "callout_status",
        "answer_callout",
        "report_unavailable",
        "clear_unavailable",
        "report_disruption",
        "my_reports",
        "show_disruption",
        "crew_risk",
        "my_queue",
        "about_me",
        "crew_payments",
        "payment_status",
        "payout_account",
    }
    assert other.is_error
