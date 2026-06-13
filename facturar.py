"""Generate a CFDI invoice for a ticket, guided by a playbook.

    uv run facturar.py ticket.json                # supervised: stops before submit
    uv run facturar.py ticket.json --auto-submit  # unattended: emits the invoice

Flow: load guides → match ticket → pre-flight → run the guarded browser agent.
Supervised (default): the agent stops at the final-submit screen and the browser
stays open so YOU verify and click the final button yourself. Auto-submit: the
agent clicks it and must capture the portal's confirmation (judge-checked).
Exit codes: 0 ready_for_review/submitted, 2 no_match, 3 conflict,
4 preflight_failed, 5 aborted/already_invoiced, 6 incomplete_max_steps,
7 judge_failed.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from cfdi.guides import GuideError, generic_guide, load_guides
from cfdi.matcher import extract_signals, match
from cfdi.preflight import get_path, interpret_purchase_date, preflight
from cfdi.runner import (
    STATUS_EXIT_CODES, notify_failure, run_agent, write_hint_draft, write_report,
)

BASE = Path(__file__).parent


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticket", type=Path, help="ticket JSON file")
    parser.add_argument("--fiscal", type=Path, default=BASE / "fiscal.json")
    guide_sel = parser.add_mutually_exclusive_group()
    guide_sel.add_argument("--guide", help="force a guide id, skipping matching")
    guide_sel.add_argument("--no-guide", action="store_true",
                           help="ignore any matching guide; run adaptive generic mode "
                                "(supervised only) — useful to test adaptation or when a "
                                "guide is stale")
    parser.add_argument("--headless", action="store_true",
                        help="run headless (default: visible, also via HEADLESS env)")
    parser.add_argument("--auto-submit", action="store_true",
                        help="UNATTENDED MODE: the agent clicks the final submit and "
                             "emits the invoice itself (no human stop). A stamped CFDI "
                             "is only undone via SAT cancellation.")
    parser.add_argument("--model", default=os.getenv("INVOICE_MODEL", "gpt-5.4"))
    args = parser.parse_args()

    guides = load_guides(BASE / "guides")
    ticket = json.loads(args.ticket.read_text(encoding="utf-8"))
    fiscal = json.loads(args.fiscal.read_text(encoding="utf-8"))

    if args.no_guide:
        try:
            guide = generic_guide(ticket)
        except GuideError as exc:
            print(f"cannot run: {exc}")
            return STATUS_EXIT_CODES["no_match"]
        print(f"ignoring any matching guide (--no-guide) — ADAPTIVE generic mode, "
              f"starting at {guide.portal_url}")
        print("  best-effort final-submit gate; a hint draft is written on success.")
    elif args.guide:
        by_id = {g.id: g for g in guides}
        if args.guide not in by_id:
            print(f"unknown guide {args.guide!r}; available: {', '.join(sorted(by_id))}")
            return STATUS_EXIT_CODES["no_match"]
        guide = by_id[args.guide]
    else:
        signals = extract_signals(ticket)
        result = match(signals, guides)
        if result.status == "conflict":
            print(f"matching conflict: ticket domain and issuer RFC point at different guides "
                  f"{result.candidates}. Resolve with --guide.")
            return STATUS_EXIT_CODES["conflict"]
        if result.status == "no_match":
            try:
                guide = generic_guide(ticket)
            except GuideError as exc:
                print(f"cannot run: {exc}")
                return STATUS_EXIT_CODES["no_match"]
            print(f"no guide for this portal — ADAPTIVE generic mode, starting at "
                  f"{guide.portal_url}")
            print("  best-effort final-submit gate; a hint draft is written on success.")
        else:
            guide = next(g for g in guides if g.id == result.guide_id)
            print(f"matched guide: {guide.id} (tier: {result.tier})")

    problems = preflight(ticket, fiscal, guide, today=date.today())
    if problems:
        print(f"pre-flight failed — fix before any browser opens ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        return STATUS_EXIT_CODES["preflight_failed"]

    raw_date = get_path(ticket, dict(guide.ticket_field_map)["purchase_date"])
    if raw_date is not None:
        _, note = interpret_purchase_date(str(raw_date), date.today())
        if note:
            print(f"note: {note}")

    headless = args.headless or bool(os.getenv("HEADLESS", "").strip())
    auto_submit = args.auto_submit
    if auto_submit and guide.is_generic:
        print("⚠️  AUTO-SUBMIT on an UNKNOWN portal (no guide): the agent identifies and")
        print("    clicks the submit button itself — no precise stop label — then must")
        print("    capture the portal's confirmation (judge-checked). Riskiest mode;")
        print("    pre-flight validated your data. Proceeding as requested.")
    elif auto_submit:
        print("AUTO-SUBMIT mode: the agent will emit the invoice itself.")
    report = run_agent(guide, ticket, fiscal, headless=headless, model=args.model,
                       auto_submit=auto_submit)
    report_path = write_report(report, ticket, guide, BASE / "runs")

    # Self-authoring: a successful first encounter teaches the portal's real flow.
    if guide.is_generic and report["status"] in ("ready_for_review", "submitted"):
        draft = write_hint_draft(guide, ticket, report, BASE / "guides" / "_drafts")
        print(f"self-authored hint draft: {draft}")
        print("  review it (esp. stop.before_labels) and move into guides/ to make this "
              "portal precise and repeatable.")

    print(f"\nstatus: {report['status']}")
    print(f"report: {report_path}")

    if report["status"] not in ("ready_for_review", "submitted"):
        webhook = os.getenv("INVOICE_FAILURE_WEBHOOK_URL", "").strip()
        if webhook:
            persisted = json.loads(report_path.read_text(encoding="utf-8"))
            if notify_failure({**persisted, "report_file": report_path.name}, webhook):
                print("failure report POSTed to INVOICE_FAILURE_WEBHOOK_URL")
    if report["status"] == "submitted":
        print("=" * 70)
        print("INVOICE EMITTED — portal confirmation:")
        print(report.get("confirmation", "(see report)"))
        print("=" * 70)
    if report.get("_held_open"):
        print("=" * 70)
        print(f"STOPPED BEFORE FINAL SUBMIT — review the open browser window, then")
        print(f"click '{report.get('human_next_button', guide.stop_before_labels[0])}' yourself.")
        print("=" * 70)
        try:
            if sys.stdin and sys.stdin.isatty():
                input("Press Enter here to close the browser when you're done... ")
        except (EOFError, KeyboardInterrupt):
            pass
    elif report["status"] == "ready_for_review":
        print("form filled and stopped before submit (browser closed; rerun headed to verify).")

    return STATUS_EXIT_CODES.get(report["status"], 5)


if __name__ == "__main__":
    sys.exit(main())
