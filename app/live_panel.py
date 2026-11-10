"""The live Investigate panel for tab 4: the AI investigates one address with Precisely's tools."""

from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
from typing import Any, Callable

import streamlit as st

from address_intelligence.app_data import highlight_changed_words
from address_intelligence.live_ai import (
    LIMITS_NOTE,
    DirectRoute,
    Investigator,
    LiveAIError,
    McpRoute,
    Step,
    format_fix,
    made_up_investigation,
    record_decision,
)

NEXT_STEP_LABELS = {
    "approve_fix": "Analyst approves the fix",
    "customer_outreach": "Ask the customer",
    "analyst_research": "Analyst research",
    "no_change_needed": "No change needed",
}
HELP = (
    "The AI investigates this one address. It calls Precisely's address, location and flood tools "
    "through the AI Studio MCP connector, explains what it found and proposes a fix, which Precisely "
    "re-checks. Nothing changes in a policy record."
)


def live_settings(root: Path) -> dict[str, Any]:
    """Read the live AI settings. Values come from the environment, then the local .env."""
    try:
        from dotenv import dotenv_values

        file_values = dotenv_values(root / ".env")
    except Exception:  # missing file or package
        file_values = {}

    def value(name: str, default: str = "") -> str:
        return os.environ.get(name) or str(file_values.get(name) or "") or default

    return {
        "enabled": value("AIP_LIVE_AI") == "1",
        "route": value("AIP_LIVE_ROUTE", "mcp").lower(),
        "fixed_order": value("AIP_LIVE_ORDER", "ai").lower() == "fixed",
        "model": value("AIP_LIVE_MODEL", "openai/gpt-oss-120b"),
        "mcp_token": value("PRECISELY_MCP_APP_TOKEN"),
        "groq_key": value("GROQ_API_KEY"),
        "has_direct_keys": bool(value("PRECISELY_API_KEY") and value("PRECISELY_API_SECRET")),
    }


def _ready(settings: dict[str, Any]) -> bool:
    if not settings["groq_key"]:
        return False
    if settings["route"] == "direct":
        return settings["has_direct_keys"]
    return bool(settings["mcp_token"])


def _build_route(settings: dict[str, Any]):
    if settings["route"] == "direct":
        from address_intelligence.config import Settings
        from address_intelligence.credits import CreditLedger
        from address_intelligence.providers.precisely import PreciselyClient

        return DirectRoute(PreciselyClient(Settings.from_env(), CreditLedger()))
    return McpRoute(settings["mcp_token"])


def _chip(text: str, *, done: bool = True) -> str:
    style = (
        "background:#F5EFFC;border:1px solid transparent"
        if done
        else "background:transparent;border:1px solid #8017E1"
    )
    return f'<span style="display:inline-block;padding:4px 10px;border-radius:999px;{style};color:#1B1433">{escape(text)}</span>'


def _panel(label: str, body_html: str, *, tinted: bool = False) -> str:
    background = "#F5EFFC" if tinted else "#FFFFFF"
    return (
        f'<div class="ai-case-panel" style="background:{background};border:1px solid #E6E3F0;'
        f'border-radius:10px;padding:12px 14px;min-width:0">'
        f'<div style="font-size:13px;color:#5F5A73;margin-bottom:6px">{escape(label)}</div>'
        f'<div style="color:#1B1433;line-height:1.5">{body_html}</div></div>'
    )


def render_result(result: dict[str, Any], *, case_key: str, decision_log: Path | None) -> None:
    """Show a finished investigation: finding, fix, Precisely's re-check, flow, limits, decision."""
    fix = result.get("proposed_fix")
    fix_text = format_fix(fix) if fix else ""
    confirmed = result.get("fix_confirmed")
    if fix:
        verdict = "Confirmed by Precisely" if confirmed else "Not confirmed by Precisely"
        color = ("#39006B", "#F5EFFC") if confirmed else ("#B00062", "#FDE8F3")
        verdict_html = (
            f'<span style="display:inline-block;border-radius:999px;padding:3px 10px;'
            f'color:{color[0]};background:{color[1]};font-weight:600">{verdict}</span>'
        )
    else:
        verdict_html = ""
    panels = [
        _panel("What the AI found", escape(result.get("finding") or "")),
        _panel("Likely cause", escape(result.get("likely_cause") or "")),
        _panel("Why it matters", escape(result.get("why_it_matters") or "")),
    ]
    if fix:
        panels.append(_panel(
            "Proposed fix",
            f'{highlight_changed_words(fix_text, result.get("address", ""))}<div style="margin-top:8px">{verdict_html}</div>',
            tinted=True,
        ))
    else:
        panels.append(_panel("Proposed fix", escape("No safe fix from the data. This goes to " + NEXT_STEP_LABELS.get(result.get("next_step", ""), "an analyst").lower() + ".")))
    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px">'
        + "".join(panels)
        + "</div>",
        unsafe_allow_html=True,
    )
    if result.get("guardrail_note"):
        st.caption(result["guardrail_note"])

    stages = [_chip("Precisely checked the address"), _chip("AI investigated")]
    if fix and confirmed:
        stages.append(_chip("Precisely confirmed the fix"))
    stages.append(_chip("Analyst decides", done=False))
    st.markdown(
        '<div class="ai-case-flow" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0">'
        + ' <span aria-hidden="true">→</span> '.join(stages)
        + "</div>",
        unsafe_allow_html=True,
    )
    meta = f'Route: {result.get("route", "")}. AI model: {result.get("model", "")}. '
    meta += f'Precisely calls: {result.get("tool_calls", 0)}. Time: {result.get("seconds", 0)} s.'
    st.caption(meta)
    with st.expander("What the AI received"):
        st.caption("Only the address and Precisely's answers about it. No names or policy details.")
        st.code(json.dumps(result.get("ai_input", {}), indent=2), language="json")
    st.info(LIMITS_NOTE)

    if decision_log is None or result.get("made_up"):
        return
    first, second = st.columns(2)
    decision = None
    if fix and confirmed:
        if first.button("Approve the fix", key=f"approve_{case_key}", type="primary", icon=":material/check:"):
            decision = "approved"
        if second.button("Reject the fix", key=f"reject_{case_key}", icon=":material/close:"):
            decision = "rejected"
    else:
        if fix:
            st.caption("Precisely did not confirm this fix, so it cannot be approved.")
        if result.get("next_step") == "customer_outreach":
            if first.button("Send to customer outreach", key=f"outreach_{case_key}", type="primary", icon=":material/mail:"):
                decision = "sent_to_customer"
        elif result.get("next_step") == "no_change_needed":
            if first.button("Mark as reviewed", key=f"reviewed_{case_key}", type="primary", icon=":material/check:"):
                decision = "reviewed"
        else:
            if first.button("Send to research", key=f"research_{case_key}", type="primary", icon=":material/manage_search:"):
                decision = "sent_to_research"
    if decision:
        record_decision(decision_log, case_id=case_key, decision=decision, investigation=result)
        st.session_state[f"live_decision_{case_key}"] = decision
    recorded = st.session_state.get(f"live_decision_{case_key}")
    if recorded:
        label = {
            "approved": "approved",
            "rejected": "rejected",
            "sent_to_research": "sent to research",
            "sent_to_customer": "sent to customer outreach",
            "reviewed": "reviewed",
        }.get(recorded, recorded)
        st.success(f"Decision recorded ({label}) for the pilot audit trail. No policy record was changed.")


def render_investigate(
    *,
    case_key: str,
    address: str,
    real_mode: bool,
    root: Path,
    run_dir: Path,
    saved_fallback: Callable[[], None] | None = None,
    allow_typed: bool = False,
) -> None:
    """The Investigate control and its result, for one case or a typed address."""
    settings = live_settings(root)
    if not settings["enabled"]:
        return
    mode = "real" if real_mode else "made_up"
    result_key = f"live_result_{mode}_{case_key}"
    pending_key = f"live_pending_{mode}_{case_key}"

    if allow_typed:
        address = st.text_input(
            "Address to investigate",
            value=st.session_state.get(f"live_address_{case_key}", ""),
            key=f"live_address_{case_key}",
            placeholder="For example: 1700 District Ave, Burlington, MA 01803",
        )

    if allow_typed and not real_mode:
        st.caption("Switch Show to Real results on this computer to investigate a typed address. Made-up mode never invents a result for a real address.")
        return

    if real_mode and not _ready(settings):
        st.caption("Live AI is not set up on this computer.")
        return
    if real_mode and settings["route"] != "direct":
        try:  # load the MCP package before the click, so the first run is not slower
            import mcp.client.streamable_http  # noqa: F401
        except ImportError:
            st.caption("Live AI needs the MCP package on this computer.")
            return

    if st.button(
        "Ask AI to investigate",
        key=f"investigate_{case_key}",
        icon=":material/manage_search:",
        help=HELP,
        disabled=not (address or "").strip(),
    ):
        st.session_state[pending_key] = True
        st.session_state.pop(result_key, None)
        st.session_state.pop(f"live_decision_{case_key}", None)

    if st.session_state.get(pending_key):
        if not real_mode:
            with st.status("Investigating (made-up example)…", expanded=True) as status:
                for step in made_up_investigation(address)["steps"]:
                    st.write(step["label"] + "…")
                status.update(label="Made-up example: no calls were made.", state="complete")
            st.session_state[result_key] = made_up_investigation(address)
            st.session_state[pending_key] = False
        else:
            route_label = "Precisely's AI Studio MCP connector" if settings["route"] != "direct" else "Precisely's APIs (backup route)"
            st.caption(f"This runs the AI on one address and calls Precisely through {route_label}.")
            confirm, cancel = st.columns([3, 1])
            go = confirm.button("Confirm: run the live investigation", key=f"confirm_{case_key}", type="primary")
            if cancel.button("Cancel", key=f"cancel_{case_key}"):
                st.session_state[pending_key] = False
                st.rerun()
            if go:
                st.session_state[pending_key] = False
                with st.status("Investigating with Precisely…", expanded=True) as status:
                    def on_step(step: Step) -> None:
                        st.write(f"{step.label}: done in {step.seconds:.1f} s")

                    route = None
                    live_result = None
                    try:
                        route = _build_route(settings)
                        if hasattr(route, "__enter__"):
                            route.__enter__()
                        investigator = Investigator(
                            route,
                            groq_api_key=settings["groq_key"],
                            model=settings["model"],
                            fixed_order=settings["fixed_order"],
                            on_step=on_step,
                            usage_log=run_dir / "live_ai" / "usage.jsonl",
                        )
                        st.write("The AI is reading the address…")
                        live_result = investigator.investigate(address).as_dict()
                    except LiveAIError as error:
                        status.update(label="Live service unavailable", state="error")
                        st.session_state[result_key] = {"fallback": True, "reason": str(error)}
                    except Exception:
                        status.update(label="Live service unavailable", state="error")
                        st.session_state[result_key] = {"fallback": True, "reason": "unexpected error"}
                    finally:
                        if route is not None and hasattr(route, "__exit__"):
                            route.__exit__(None, None, None)
                    if live_result is not None:
                        status.update(label=f"Investigation complete in {live_result['seconds']} s", state="complete")
                        st.session_state[result_key] = live_result
                        _save_result(run_dir, case_key, live_result)

    result = st.session_state.get(result_key)
    if not result:
        return
    if result.get("fallback"):
        st.warning("Live service unavailable. Showing the result from the last run.")
        if saved_fallback is not None:
            saved_fallback()
        else:
            st.caption("No saved result exists for a typed address. Try again in a moment.")
        return
    render_result(
        result,
        case_key=case_key,
        decision_log=(run_dir / "live_ai" / "decisions.jsonl") if real_mode else None,
    )


def _save_result(run_dir: Path, case_key: str, result: dict[str, Any]) -> None:
    from datetime import datetime

    folder = run_dir / "live_ai"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_key = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in case_key)[:60]
    (folder / f"{safe_key}_{stamp}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
