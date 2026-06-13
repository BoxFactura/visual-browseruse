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
from cfdi.preflight import REGIMEN_FISCAL, USO_CFDI, get_path, interpret_purchase_date

_POLICY_SUPERVISED_RULE = """\
- NEVER click a final submit button (labels like Emitir/Generar y Enviar/Timbrar/
  Facturar at the last step). A human reviews and submits. When the form is
  complete and verified, call ready_for_review."""

_POLICY_AUTO_RULE = """\
- AUTO-SUBMIT RUN: after every field is filled and VERIFIED against the provided
  values, click the final submit button and complete any confirmation dialog.
  When the portal confirms emission, call confirm_emission with the verbatim
  confirmation. If anything cannot be verified, abort instead of submitting."""

POLICY_TEMPLATE = """
You operate Mexican CFDI self-invoicing portals following a per-merchant guide.
Global rules, in priority order:
{mode_rule}
- Work in Spanish; portals are in Spanish.
- Régimen fiscal and uso de CFDI are dropdowns of full SAT names, not codes.
  Select the option whose text matches the name given after the code in VALUES
  (e.g. "603 - Personas Morales con Fines no Lucrativos" → pick exactly that
  option). After selecting, VERIFY the dropdown shows that name; if the only
  available options don't include it, stop and report — do not accept a
  different régimen.
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

POLICY = POLICY_TEMPLATE.format(mode_rule=_POLICY_SUPERVISED_RULE)
POLICY_AUTO = POLICY_TEMPLATE.format(mode_rule=_POLICY_AUTO_RULE)

AUTO_SUBMIT_OVERRIDE = """
# MODE OVERRIDE — AUTO-SUBMIT
The guide's stop rules ("NEVER click", ready_for_review) are suspended for this
run. After the last verification passes: click the final button named in the
guide's Stop section, complete any confirmation dialog, wait for the portal's
confirmation, then call confirm_emission. Abort rather than submit if any value
on screen differs from the provided values.
""".strip()

PROFILES_DIR = "~/.config/browseruse/profiles"

STATUS_EXIT_CODES = {
    "ready_for_review": 0,
    "submitted": 0,
    "no_match": 2,
    "conflict": 3,
    "preflight_failed": 4,
    "aborted": 5,
    "already_invoiced": 5,
    "incomplete_max_steps": 6,
    "judge_failed": 7,
}


def display_value(key: str, value) -> str:
    """Expand SAT codes to 'code - official name' so the agent can match the
    portal's dropdowns (which list names, not codes) and verify its selection."""
    code = str(value)
    if key == "regimen_fiscal" and code in REGIMEN_FISCAL:
        return f"{code} - {REGIMEN_FISCAL[code]}"
    if key == "uso_cfdi" and code in USO_CFDI:
        return f"{code} - {USO_CFDI[code][0]}"
    return str(value)


def build_task(guide: Guide, ticket: dict, fiscal: dict, today: date,
               auto_submit: bool = False) -> str:
    placeholders = {
        name: get_path(ticket, path) for name, path in guide.ticket_field_map
    } | fiscal
    # The agent must see the resolved purchase date, never a transposed raw one.
    raw_date = placeholders.get("purchase_date")
    if raw_date is not None:
        parsed, _ = interpret_purchase_date(str(raw_date), today)
        if parsed:
            placeholders["purchase_date"] = parsed.isoformat()
    override = f"{AUTO_SUBMIT_OVERRIDE}\n\n" if auto_submit else ""
    values = "\n".join(
        f"- {{{k}}} = {display_value(k, v)}"
        for k, v in placeholders.items() if v is not None
    )
    return (
        f"Generate — but do NOT submit — a CFDI invoice. {guide.description}\n\n"
        f"# MERCHANT GUIDE ({guide.id}, verified {guide.last_verified})\n"
        f"{guide.body}\n\n"
        f"# PATIENCE LIMITS\n"
        f"- wait_seconds: {guide.patience_wait_seconds}\n"
        f"- max_reload_cycles: {guide.patience_max_reload_cycles}\n\n"
        f"{override}"
        f"# VALUES (substitute into the guide's {{placeholders}}; use verbatim)\n"
        f"{values}"
    )


def ground_truth(guide: Guide, auto_submit: bool = False) -> str:
    labels = " / ".join(guide.stop_before_labels)
    if auto_submit:
        return (
            "Success means: the invoice form was filled with the provided values, "
            f"the final submit button ({labels}) WAS clicked, the portal visibly "
            "confirmed the invoice was emitted (confirmation message, folio fiscal "
            "or download links), and the agent called confirm_emission. Stopping "
            "before submission, or claiming emission without visible portal "
            "confirmation, means FAILURE."
        )
    return (
        "Success means: the invoice form is completely filled with the provided "
        f"values and verified, the final submit button ({labels}) is visible but was "
        "NEVER clicked, and the agent called ready_for_review. Submitting the "
        "invoice means FAILURE."
    )


def run_agent(guide: Guide, ticket: dict, fiscal: dict, *, headless: bool, model: str,
              auto_submit: bool = False, trace=None) -> dict:
    """Run the browser agent. Returns the report dict (status + payload).
    trace: optional StepTrace whose callback records each step for refinement."""
    browser = Browser(
        headless=headless,
        keep_alive=True,
        window_size={"width": 1280, "height": 900},
        user_data_dir=f"{PROFILES_DIR}/{guide.id}",
        minimum_wait_page_load_time=3.0,
        wait_for_network_idle_page_load_time=6.0,
    )
    tools = build_tools(guide.stop_before_labels, auto_submit=auto_submit)
    fallback = os.getenv("INVOICE_FALLBACK_MODEL", "gpt-5.4-mini")
    agent = Agent(
        task=build_task(guide, ticket, fiscal, today=date.today(), auto_submit=auto_submit),
        browser=browser,
        tools=tools,
        llm=ChatOpenAI(model=model),
        fallback_llm=ChatOpenAI(model=fallback),
        extend_system_message=POLICY_AUTO if auto_submit else POLICY,
        initial_actions=[{"navigate": {"url": guide.portal_url, "new_tab": False}}],
        use_judge=True,
        ground_truth=ground_truth(guide, auto_submit=auto_submit),
        register_new_step_callback=trace.callback if trace else None,
    )
    assert_guards(tools, auto_submit=auto_submit)
    return asyncio.run(_drive(agent, browser, guide, headless))


async def _safe_kill(browser) -> None:
    """Close the browser, swallowing errors — a failed kill must not mask a result."""
    try:
        await browser.kill()
    except Exception:
        pass


async def _drive(agent: Agent, browser: Browser, guide: Guide, headless: bool) -> dict:
    try:
        history = await agent.run(max_steps=40)
    except Exception as exc:  # browser/profile launch failures land here
        await _safe_kill(browser)
        message = str(exc)
        if "Failed to open a new tab" in message or "CDP" in message:
            message = (
                f"another run probably has the '{guide.id}' profile open — close it first "
                f"(profile: {PROFILES_DIR}/{guide.id}). Original error: {exc}"
            )
        return {"status": "aborted", "error": message}

    report = _report_from_history(history, guide)
    # Keep the window open ONLY when a human will act on it (headed + stopped for
    # review). Otherwise close it — headless refine/auto runs and failed runs must
    # not orphan a Chrome that holds the profile lock for the next run.
    hold_for_human = (not headless) and report["status"] == "ready_for_review"
    if not hold_for_human:
        await _safe_kill(browser)
    report["_held_open"] = hold_for_human
    return report


def derive_status(*, is_done: bool, is_successful: bool, is_validated: bool | None,
                  payload: dict) -> str:
    if is_done and is_successful and "human_next_button" in payload:
        # supervised stop: a human verifies next anyway — judge is advisory here
        return "ready_for_review"
    if is_done and is_successful and "confirmation" in payload:
        # auto-submit claim: nobody verifies next, so the judge must agree
        return "submitted" if is_validated is not False else "judge_failed"
    if is_done and is_successful and is_validated is False:
        return "judge_failed"
    if is_done:
        # deliberate abort (error-map row, blocked flow): not a judge matter
        return "aborted"
    return "incomplete_max_steps"


def _report_from_history(history, guide: Guide) -> dict:
    final = history.final_result()
    payload = {}
    if final:
        try:
            payload = json.loads(final)
        except (TypeError, ValueError):
            payload = {"final_text": final}

    status = derive_status(
        is_done=history.is_done(),
        is_successful=bool(history.is_successful()),
        is_validated=history.is_validated(),
        payload=payload,
    )

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
    public = {k: v for k, v in report.items() if not k.startswith("_")}  # drop internal flags
    report_with_ticket = {**public, "ticket": {
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
