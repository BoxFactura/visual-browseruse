"""Generate (but never submit) a CFDI invoice for a ticket, guided by a playbook.

    uv run facturar.py tickets/san-pablo-2026-06-01.json

Flow: load guides → match ticket → pre-flight → run the guarded browser agent →
stop at the final-submit screen → the browser stays open so YOU verify and click
the final button yourself. Exit codes: 0 ready_for_review, 2 no_match,
3 conflict, 4 preflight_failed, 5 aborted/already_invoiced,
6 incomplete_max_steps, 7 judge_failed.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from cfdi.guides import load_guides
from cfdi.matcher import extract_signals, match
from cfdi.preflight import preflight
from cfdi.runner import STATUS_EXIT_CODES, run_agent, write_report

BASE = Path(__file__).parent


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticket", type=Path, help="ticket JSON file")
    parser.add_argument("--fiscal", type=Path, default=BASE / "fiscal.json")
    parser.add_argument("--guide", help="force a guide id, skipping matching")
    parser.add_argument("--headless", action="store_true",
                        help="run headless (default: visible, also via HEADLESS env)")
    parser.add_argument("--model", default=os.getenv("INVOICE_MODEL", "gpt-5.4"))
    args = parser.parse_args()

    guides = load_guides(BASE / "guides")
    ticket = json.loads(args.ticket.read_text(encoding="utf-8"))
    fiscal = json.loads(args.fiscal.read_text(encoding="utf-8"))

    if args.guide:
        by_id = {g.id: g for g in guides}
        if args.guide not in by_id:
            print(f"unknown guide {args.guide!r}; available: {', '.join(sorted(by_id))}")
            return STATUS_EXIT_CODES["no_match"]
        guide = by_id[args.guide]
    else:
        signals = extract_signals(ticket)
        result = match(signals, guides)
        if result.status == "no_match":
            print(f"no guide matches this ticket (signals: domain={signals.domain!r}, "
                  f"rfc={signals.rfc!r}). Write a guide or force one with --guide.")
            return STATUS_EXIT_CODES["no_match"]
        if result.status == "conflict":
            print(f"matching conflict: ticket domain and issuer RFC point at different guides "
                  f"{result.candidates}. Resolve with --guide.")
            return STATUS_EXIT_CODES["conflict"]
        guide = next(g for g in guides if g.id == result.guide_id)
        print(f"matched guide: {guide.id} (tier: {result.tier})")

    problems = preflight(ticket, fiscal, guide, today=date.today())
    if problems:
        print(f"pre-flight failed — fix before any browser opens ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        return STATUS_EXIT_CODES["preflight_failed"]

    headless = args.headless or bool(os.getenv("HEADLESS", "").strip())
    report = run_agent(guide, ticket, fiscal, headless=headless, model=args.model)
    report_path = write_report(report, ticket, BASE / "runs")

    print(f"\nstatus: {report['status']}")
    print(f"report: {report_path}")
    if report["status"] == "ready_for_review":
        print("=" * 70)
        print(f"STOPPED BEFORE FINAL SUBMIT — review the open browser window, then")
        print(f"click '{report.get('human_next_button', guide.stop_before_labels[0])}' yourself.")
        print("=" * 70)
        try:
            if sys.stdin and sys.stdin.isatty():
                input("Press Enter here to close the browser when you're done... ")
        except (EOFError, KeyboardInterrupt):
            pass

    return STATUS_EXIT_CODES.get(report["status"], 5)


if __name__ == "__main__":
    sys.exit(main())
