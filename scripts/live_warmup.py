"""Warm-up check for the live AI demo. Free by default: no paid action is run.

Checks, in order:
1. The settings in .env (names only, never values).
2. Precisely's AI Studio MCP connector: connect, list its tools, and describe the
   three actions the app uses. Describing is free.
3. The AI service key (Groq), by listing its models. Free.
4. Resets the work list for the demo: decision logs are moved aside (never deleted),
   so every approved case is back on the list. Use --keep-decisions to skip this.

With --live, it also runs ONE real investigation on Precisely's HQ, keyed in with
three errors, and reports the time. That uses about 4 AI Studio credits, so only
run it when David has said YES.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values  # noqa: E402

from address_intelligence.live_ai import MCP_URL, TOOL_ACTIONS  # noqa: E402
from address_intelligence.worklist_decisions import archive_logs  # noqa: E402

DEMO_ADDRESS = "1700 Distrct Avenu, Burlingtn, MA 01840"
REQUIRED = ("PRECISELY_MCP_APP_TOKEN", "GROQ_API_KEY", "AIP_LIVE_AI")
OK, FAIL = "PASS", "FAIL"


def check_settings(values: dict[str, str | None]) -> list[tuple[str, bool, str]]:
    results = []
    for name in REQUIRED:
        present = bool(values.get(name))
        detail = "set" if present else "missing"
        if name == "AIP_LIVE_AI" and present and values.get(name) != "1":
            present, detail = False, "must be 1"
        results.append((f"Setting {name}", present, detail))
    return results


async def check_connector(token: str) -> list[tuple[str, bool, str]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    results = []
    started = time.perf_counter()
    client = create_mcp_http_client(headers={"Authorization": f"Apikey {token}"})
    async with client:
        async with streamable_http_client(MCP_URL, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = {tool.name for tool in (await session.list_tools()).tools}
                results.append((
                    "Connector reachable",
                    True,
                    f"{time.perf_counter() - started:.1f} s to connect and list tools",
                ))
                needed = {"precisely_actions_describe", "precisely_actions_execute"}
                results.append(("Connector tools", needed <= tools, ", ".join(sorted(tools)) or "none"))
                for action in TOOL_ACTIONS.values():
                    began = time.perf_counter()
                    answer = await session.call_tool("precisely_actions_describe", {"action_id": action})
                    text = "\n".join(getattr(block, "text", "") or "" for block in (answer.content or []))
                    try:
                        schema = json.loads(text).get("inputs_schema", {})
                        has_address = "address" in schema.get("properties", {})
                    except ValueError:
                        has_address = False
                    results.append((
                        f"Action {action}",
                        has_address,
                        f"takes an address ({time.perf_counter() - began:.1f} s)" if has_address else "missing or changed",
                    ))
    return results


def check_ai(key: str) -> tuple[str, bool, str]:
    import requests

    try:
        response = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
    except requests.RequestException:
        return ("AI service key", False, "AI service did not answer")
    return ("AI service key", response.status_code == 200, f"status {response.status_code}")


def run_live(values: dict[str, str | None]) -> tuple[str, bool, str]:
    from address_intelligence.live_ai import Investigator, McpRoute

    route = McpRoute(values["PRECISELY_MCP_APP_TOKEN"] or "")
    with route:
        investigator = Investigator(
            route,
            groq_api_key=values["GROQ_API_KEY"] or "",
            model=values.get("AIP_LIVE_MODEL") or "openai/gpt-oss-120b",
            usage_log=ROOT / "data" / "output" / "run" / "live_ai" / "usage.jsonl",
        )
        result = investigator.investigate(DEMO_ADDRESS)
    fixed = result.fix_confirmed is True
    return (
        "Live demo run (HQ, three errors)",
        fixed and result.seconds < 20,
        f"{result.seconds:.1f} s, fix confirmed: {fixed}, Precisely calls: {result.tool_calls}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="also run one paid demo investigation")
    parser.add_argument("--keep-decisions", action="store_true", help="keep the analyst decisions on the work list")
    args = parser.parse_args(argv)

    values = dotenv_values(ROOT / ".env")
    rows = check_settings(values)
    if values.get("PRECISELY_MCP_APP_TOKEN"):
        try:
            rows += asyncio.run(asyncio.wait_for(check_connector(values["PRECISELY_MCP_APP_TOKEN"]), 45))
        except Exception as error:  # report, never show the token
            rows.append(("Connector reachable", False, type(error).__name__))
    if values.get("GROQ_API_KEY"):
        rows.append(check_ai(values["GROQ_API_KEY"]))
    if not args.keep_decisions:
        moved = archive_logs(ROOT / "data" / "output")
        rows.append((
            "Work list reset",
            True,
            f"{len(moved)} decision log(s) moved aside" if moved else "already fresh",
        ))
    if args.live:
        try:
            rows.append(run_live(values))
        except Exception as error:
            rows.append(("Live demo run (HQ, three errors)", False, type(error).__name__))

    print("\nLive AI warm-up\n")
    for name, passed, detail in rows:
        print(f"  {OK if passed else FAIL}  {name}: {detail}")
    all_passed = all(passed for _, passed, _ in rows)
    print("\n  Ready for the demo." if all_passed else "\n  Not ready. Fix the FAIL lines, or use the backup video.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
