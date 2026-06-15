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
- Régimen fiscal is a dropdown of full SAT names — select the option matching the
  name after the code in VALUES, then verify it; if it isn't offered, stop and report.
- Uso de CFDI in VALUES may be an ORDERED list ("A - ... then B - ..."). Select the
  FIRST listed uso that the dropdown actually offers — portals often don't offer every
  uso. If none of the listed usos are offered, stop and report.
- Amount fields: enter them with type_slowly using the EXACT decimal value (e.g. type
  "2306.00") — type_slowly types real keystrokes with a delay and blurs, so a masked field
  formats correctly. Then VERIFY the field shows that amount (e.g. $2,306.00), NEVER the raw
  digits like 230600. If it's still wrong, retry type_slowly with a larger delay.
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

def auto_submit_override(stop_labels: tuple[str, ...]) -> str:
    labels = " / ".join(f'"{s}"' for s in stop_labels) or "Generar / Emitir / Facturar / Timbrar"
    return (
        "# MODE OVERRIDE — AUTO-SUBMIT (this run ISSUES the invoice)\n"
        "Any \"NEVER click\" or \"call ready_for_review\" wording is for supervised runs "
        "and is SUSPENDED here. In THIS run, once every field is filled and verified "
        "against the values:\n"
        f"1. Identify and click the button that ISSUES the invoice (likely labeled {labels}).\n"
        "2. Complete any confirmation dialog (e.g. Aceptar / Confirmar).\n"
        "3. Wait for the portal's emission confirmation (folio fiscal / UUID, download "
        "links, or a success message).\n"
        "4. Call confirm_emission with that verbatim confirmation. Do NOT call done or "
        "ready_for_review.\n"
        "Abort (do not submit) only if an on-screen value differs from the values provided."
    )


def _strip_supervised_stop(body: str) -> str:
    """Drop the guide's supervised '## Stop & completion' prose for auto-submit runs,
    so its 'NEVER click' instruction can't contradict the submit override."""
    return body.split("\n## Stop & completion")[0].rstrip()

PROFILES_DIR = "~/.config/browseruse/profiles"

# Unpacked MV3 extension that draws a 10px black border on every page body — a
# visual marker that you're looking at the automated browser, not a normal one.
PAGE_MARKER_EXTENSION = Path(__file__).resolve().parent.parent / "extensions" / "page-border"


def _extension_args() -> list[str]:
    """Chrome args that load ONLY our page-marker extension.

    We own the extension args wholesale instead of adding to browser-use's
    bundled set: BrowserSession rebuilds the BrowserProfile from kwargs (so a
    profile subclass override wouldn't survive), and its own --load-extension is
    appended after ours and would win the launch-time arg dedup — dropping ours.
    So we disable the default extensions (see enable_default_extensions=False)
    and supply the single --load-extension here. Chrome needs an absolute path.
    """
    ext = str(PAGE_MARKER_EXTENSION)
    return [
        "--enable-extensions",
        f"--disable-extensions-except={ext}",
        f"--load-extension={ext}",
    ]

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
    if key == "uso_cfdi":
        codes = value if isinstance(value, list) else [value]
        named = [f"{c} - {USO_CFDI[c]}" if str(c) in USO_CFDI else str(c) for c in codes]
        if len(named) > 1:
            return " then ".join(named) + "  (use the FIRST the dropdown offers)"
        return named[0] if named else str(value)
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
    body = _strip_supervised_stop(guide.body) if auto_submit else guide.body
    override = f"{auto_submit_override(guide.stop_before_labels)}\n\n" if auto_submit else ""
    values = "\n".join(
        f"- {{{k}}} = {display_value(k, v)}"
        for k, v in placeholders.items() if v is not None
    )
    # If the ticket_field_map didn't resolve the core values (lean guide, or an
    # extractor shape the map doesn't fit) — or it's a generic run — hand the agent
    # the raw ticket to pull número/fecha/total from itself. No per-portal map needed.
    raw_ticket = ""
    if guide.is_generic or any(placeholders.get(k) is None for k in ("facturacion_folio", "total")):
        raw_ticket = (
            "\n\n# TICKET (raw JSON — read número/folio, fecha, and total from here "
            "as the portal asks)\n" + json.dumps(ticket, ensure_ascii=False)
        )
    label = "MERCHANT GUIDE" if not guide.is_generic else "HINTS (no specific guide — adapt)"
    opening = (
        f"Generate AND SUBMIT (issue) a CFDI invoice — this run issues it. {guide.description}"
        if auto_submit else
        f"Generate — but do NOT submit — a CFDI invoice. {guide.description}"
    )
    return (
        f"{opening}\n\n"
        f"# {label} ({guide.id}, verified {guide.last_verified})\n"
        f"{body}\n\n"
        f"# PATIENCE LIMITS\n"
        f"- wait_seconds: {guide.patience_wait_seconds}\n"
        f"- max_reload_cycles: {guide.patience_max_reload_cycles}\n\n"
        f"{override}"
        f"# VALUES (substitute into the guide's {{placeholders}}; use verbatim)\n"
        f"{values}"
        f"{raw_ticket}"
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
        enable_default_extensions=False,
        args=_extension_args(),
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


async def _safe_kill(browser, guide: Guide) -> None:
    """Close the browser and clear its (now stale) profile lock files, swallowing
    errors — a failed kill must not mask a result."""
    try:
        await browser.kill()
    except Exception:
        pass
    profile = Path(f"{PROFILES_DIR}/{guide.id}").expanduser()
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / lock).unlink()
        except (FileNotFoundError, OSError):
            pass


async def _drive(agent: Agent, browser: Browser, guide: Guide, headless: bool) -> dict:
    try:
        history = await agent.run(max_steps=40)
    except Exception as exc:  # browser/profile launch failures land here
        await _safe_kill(browser, guide)
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
        await _safe_kill(browser, guide)
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


def write_hint_draft(guide: Guide, ticket: dict, report: dict, drafts_dir: Path) -> Path:
    """Turn a successful generic (guideless) run into a reviewable hint draft.

    Captures what only a real run knows — the URL that rendered the form and the
    EXACT final-submit label the agent saw — so a human can review and promote it
    into guides/, making that portal precise and (optionally) automatable next time.
    Drafts live in guides/_drafts/ (the runner never loads them).
    """
    from cfdi.matcher import normalize_domain

    drafts_dir.mkdir(parents=True, exist_ok=True)
    slug = guide.id.removeprefix("generic-")
    domain = normalize_domain(guide.portal_url) or ""
    final_url = report.get("final_url") or guide.portal_url
    stop_label = (report.get("human_next_button") or "").strip() or "REVIEW_REQUIRED"
    issuer_rfc = (ticket.get("issuer") or {}).get("rfc") or ""
    filled = report.get("fields_filled") or "(see run report)"
    filled_block = "\n".join(f"  {line}" for line in str(filled).splitlines()) or "  (none recorded)"

    content = f"""---
id: {slug}
description: CFDI invoicing for {domain} — auto-drafted from a successful run; REVIEW.
match:
  domains: [{domain}]
  rfcs: [{issuer_rfc}]
portal_url: {final_url}
required_ticket_fields: []
required_fiscal_fields: [rfc, nombre, cp, regimen_fiscal, uso_cfdi, email]
stop:
  before_labels: ["{stop_label}"]
patience: {{ max_reload_cycles: 3, wait_seconds: 10 }}
last_verified: never
---
## Auto-drafted from a successful supervised run — REVIEW before promoting
A generic (guideless) run completed this portal. Confirm the details below, add a
`ticket_field_map` matching this merchant's ticket JSON, tighten the steps, then move
this file into guides/ and set last_verified.

## Observed in the run
- URL that rendered the form: {final_url}
- Final submit button observed: "{stop_label}"  ← this becomes the stop label; confirm it.
- Fields filled:
{filled_block}

## Steps (refine from the observed flow)
1. Start at the URL above. If it looks blank, apply the patience policy (slow SPA).
2. Fill the receptor bundle (rfc, nombre, cp, régimen, uso, email) and the ticket
   number/date/total into whatever fields this portal asks for.
3. Régimen fiscal / Uso de CFDI are dropdowns — select the option by its name.

## Error codes
| portal message contains | meaning | action |
|---|---|---|
| ya facturado / previamente facturado | this ticket already has an invoice | abort with status already_invoiced |

## Stop & completion
Final button: "{stop_label}" — NEVER click it. Call ready_for_review when only it remains.
"""
    path = drafts_dir / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
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
