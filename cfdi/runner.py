"""Assemble the focused per-merchant prompt and drive the browser-use Agent.

Prompt layout follows the plan's cache rules: the stable cross-merchant policy
lives in extend_system_message (cached system prompt); the task carries the
matched guide body VERBATIM first (stable across runs of the same guide) and
the per-run values LAST.
"""

import asyncio
import json
import os
from datetime import date, datetime
from pathlib import Path

import httpx
from browser_use import Agent, Browser, ChatOpenAI

from cfdi.guards import assert_guards, build_tools
from cfdi.guides import Guide
from cfdi.preflight import get_path, interpret_purchase_date

POLICY = """
You operate Mexican CFDI self-invoicing portals following a per-merchant guide.
Global rules, in priority order:
- NEVER click a final submit button (labels like Emitir/Generar y Enviar/Timbrar/
  Facturar at the last step). A human reviews and submits. When the form is
  complete and verified, call ready_for_review.
- Work in Spanish; portals are in Spanish.
- For currency- or number-masked fields, use the set_masked_input action with
  digits only. Never type amounts with decimal points.
- Lines starting with "expected:" in the guide describe what the tutorial
  screenshots showed — the live page wins if they disagree.
- Patience policy for slow SPAs: when a page looks blank, wait the guide's
  wait_seconds, scroll, then reload — at most max_reload_cycles times. After
  exhausting them, stop and report the portal as unreachable.
- Follow the guide's error-code table exactly: data problems are NOT navigation
  problems; do not retry them.
- Use ONLY the values provided. Never invent or alter a value.
""".strip()

PROFILES_DIR = "~/.config/browseruse/profiles"

STATUS_EXIT_CODES = {
    "ready_for_review": 0,
    "no_match": 2,
    "conflict": 3,
    "preflight_failed": 4,
    "aborted": 5,
    "already_invoiced": 5,
    "incomplete_max_steps": 6,
    "judge_failed": 7,
}


def build_task(guide: Guide, ticket: dict, fiscal: dict, today: date) -> str:
    placeholders = {
        name: get_path(ticket, path) for name, path in guide.ticket_field_map
    } | fiscal
    # The agent must see the resolved purchase date, never a transposed raw one.
    raw_date = placeholders.get("purchase_date")
    if raw_date is not None:
        parsed, _ = interpret_purchase_date(str(raw_date), today)
        if parsed:
            placeholders["purchase_date"] = parsed.isoformat()
    values = "\n".join(f"- {{{k}}} = {v}" for k, v in placeholders.items() if v is not None)
    return (
        f"Generate — but do NOT submit — a CFDI invoice. {guide.description}\n\n"
        f"# MERCHANT GUIDE ({guide.id}, verified {guide.last_verified})\n"
        f"{guide.body}\n\n"
        f"# PATIENCE LIMITS\n"
        f"- wait_seconds: {guide.patience_wait_seconds}\n"
        f"- max_reload_cycles: {guide.patience_max_reload_cycles}\n\n"
        f"# VALUES (substitute into the guide's {{placeholders}}; use verbatim)\n"
        f"{values}"
    )


def ground_truth(guide: Guide) -> str:
    labels = " / ".join(guide.stop_before_labels)
    return (
        "Success means: the invoice form is completely filled with the provided "
        f"values and verified, the final submit button ({labels}) is visible but was "
        "NEVER clicked, and the agent called ready_for_review. Submitting the "
        "invoice means FAILURE."
    )


def run_agent(guide: Guide, ticket: dict, fiscal: dict, *, headless: bool, model: str) -> dict:
    """Run the browser agent. Returns the report dict (status + payload)."""
    browser = Browser(
        headless=headless,
        keep_alive=True,
        window_size={"width": 1280, "height": 900},
        user_data_dir=f"{PROFILES_DIR}/{guide.id}",
        minimum_wait_page_load_time=3.0,
        wait_for_network_idle_page_load_time=6.0,
    )
    tools = build_tools(guide.stop_before_labels)
    fallback = os.getenv("INVOICE_FALLBACK_MODEL", "gpt-5.4-mini")
    agent = Agent(
        task=build_task(guide, ticket, fiscal, today=date.today()),
        browser=browser,
        tools=tools,
        llm=ChatOpenAI(model=model),
        fallback_llm=ChatOpenAI(model=fallback),
        extend_system_message=POLICY,
        initial_actions=[{"navigate": {"url": guide.portal_url, "new_tab": False}}],
        use_judge=True,
        ground_truth=ground_truth(guide),
    )
    assert_guards(tools)

    try:
        history = asyncio.run(agent.run(max_steps=40))
    except Exception as exc:  # browser/profile launch failures land here
        message = str(exc)
        if "Failed to open a new tab" in message or "CDP" in message:
            message = (
                f"another run probably has the '{guide.id}' profile open — close it first "
                f"(profile: {PROFILES_DIR}/{guide.id}). Original error: {exc}"
            )
        return {"status": "aborted", "error": message}

    return _report_from_history(history, guide)


def _report_from_history(history, guide: Guide) -> dict:
    final = history.final_result()
    payload = {}
    if final:
        try:
            payload = json.loads(final)
        except (TypeError, ValueError):
            payload = {"final_text": final}

    if history.is_done() and history.is_successful() and "human_next_button" in payload:
        status = "ready_for_review"
    elif history.is_done() and history.is_successful() and history.is_validated() is False:
        # the agent CLAIMED success but the judge disagreed — needs human eyes
        status = "judge_failed"
    elif history.is_done():
        # deliberate abort (error-map row, blocked flow): not a judge matter
        status = "aborted"
    else:
        status = "incomplete_max_steps"

    portal_errors = payload.get("portal_errors_verbatim", [])
    if any("ya facturado" in e.lower() or "previamente facturado" in e.lower() for e in portal_errors):
        status = "already_invoiced"

    usage = history.usage
    return {
        "status": status,
        "guide_id": guide.id,
        **payload,
        "steps_taken": history.number_of_steps(),
        "unmapped_errors": len([e for e in history.errors() if e]),
        "usage": {
            "total_tokens": usage.total_tokens if usage else None,
            "by_model": {
                name: stats.invocations for name, stats in (usage.by_model or {}).items()
            } if usage else {},
        },
    }


def write_report(report: dict, ticket: dict, guide: Guide, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    field_map = dict(guide.ticket_field_map)
    folio = get_path(ticket, field_map["facturacion_folio"]) or "no-folio"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = runs_dir / f"{stamp}-{report.get('guide_id', 'unknown')}-{folio}.json"
    report_with_ticket = {**report, "ticket": {
        "folio": folio,
        "total": get_path(ticket, field_map["total"]),
    }}
    path.write_text(json.dumps(report_with_ticket, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def notify_failure(report: dict, url: str) -> bool:
    """POST a failed run's report to the failure webhook. Never raises —
    a dead webhook must not turn a reported failure into a crash."""
    payload = {**report, "occurred_at": datetime.now().isoformat(timespec="seconds")}
    try:
        response = httpx.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"warning: failure-webhook POST failed: {exc}")
        return False
