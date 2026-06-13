"""Refine a guide from a real dry run: trace the steps, surface the stumbles.

    uv run refine.py ticket.json                 # dry run, print trace + friction
    uv run refine.py ticket.json --propose        # also draft a tightened guide

"Dry" = supervised (the agent never submits), so nothing irreversible happens —
this is safe to run repeatedly while iterating on a guide. The trace shows which
steps were wasted (waits, reloads, re-entering filled fields, dropdown fumbles)
so the guide can be tightened to cut steps and speed up the next run.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from cfdi.guides import load_guides, parse_guide
from cfdi.matcher import extract_signals, match
from cfdi.preflight import preflight
from cfdi.runner import build_task, run_agent
from cfdi.trace import StepTrace

BASE = Path(__file__).parent

PROPOSE_PROMPT = """\
You tighten a browser-agent invoicing guide using a trace of a real run.

GOAL: fewer steps and faster runs, WITHOUT weakening safety. Keep the exact
format (frontmatter + the same body sections). Specifically:
- Fold discovered waits into the step that needs them (e.g. "after clicking X the
  SPA takes a few seconds; wait for field Y") instead of leaving the agent to
  discover blankness and burn a step.
- Drop or downgrade-to-verify any field the portal already pre-fills or carries
  over between screens (the trace shows what was already present).
- Name custom widgets precisely (e.g. a régimen dropdown that is a click-to-open
  listbox, not a native select) so the agent doesn't fumble.
- NEVER change stop.before_labels, the error-code table, or any safety wording.
- Keep last_verified as-is (a human sets it after a clean supervised run).

Output ONLY the full refined guide file (frontmatter + body), no commentary."""


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticket", type=Path)
    parser.add_argument("--fiscal", type=Path, default=BASE / "fiscal.json")
    parser.add_argument("--guide", help="force a guide id")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--headed", action="store_true", help="show the browser (default headless)")
    parser.add_argument("--propose", action="store_true", help="draft a tightened guide from the trace")
    args = parser.parse_args()

    guides = load_guides(BASE / "guides")
    ticket = json.loads(args.ticket.read_text(encoding="utf-8"))
    fiscal = json.loads(args.fiscal.read_text(encoding="utf-8"))

    if args.guide:
        guide = next((g for g in guides if g.id == args.guide), None)
        if guide is None:
            print(f"unknown guide {args.guide!r}")
            return 1
    else:
        result = match(extract_signals(ticket), guides)
        if result.status != "matched":
            print(f"cannot refine: match status is {result.status} ({result.candidates or ''})")
            return 1
        guide = next(g for g in guides if g.id == result.guide_id)

    problems = preflight(ticket, fiscal, guide, today=date.today())
    if problems:
        print("pre-flight failed; fix data before refining:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"dry run (supervised, no submit) of guide '{guide.id}'...\n")
    trace = StepTrace()
    report = run_agent(guide, ticket, fiscal, headless=not args.headed,
                       model=args.model, auto_submit=False, trace=trace)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    trace_path = BASE / "runs" / f"{stamp}-{guide.id}-trace.jsonl"
    trace.write(trace_path)
    friction = trace.friction_summary()

    print("=" * 70)
    print(trace.render())
    print("=" * 70)
    print(f"outcome: {report['status']}")
    print(f"steps: {friction['total_steps']} | actions: {friction['action_counts']}")
    print(f"wasted actions (wait/scroll/navigate): {friction['wasted_action_count']}")
    print(f"friction steps (eval flagged trouble): {friction['friction_steps']}")
    print(f"trace: {trace_path}")

    if args.propose:
        from openai import OpenAI

        print("\nproposing a tightened guide...")
        client = OpenAI()
        user = (
            f"CURRENT GUIDE ({guide.id}):\n\n{guide.path.read_text(encoding='utf-8')}\n\n"
            f"TASK PROMPT THE AGENT RECEIVED:\n{build_task(guide, ticket, fiscal, today=date.today())}\n\n"
            f"RUN TRACE (outcome {report['status']}, {friction['total_steps']} steps, "
            f"wasted={friction['wasted_action_count']}):\n{trace.render()}"
        )
        response = client.chat.completions.create(
            model=args.model,
            messages=[{"role": "system", "content": PROPOSE_PROMPT}, {"role": "user", "content": user}],
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        draft_dir = BASE / "guides" / "_drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        draft_path = draft_dir / f"{guide.id}.md"
        draft_path.write_text(content.strip() + "\n", encoding="utf-8")
        try:
            refined = parse_guide(draft_path, allow_review_placeholder=True)
            print(f"refined draft: {draft_path} (stop labels: {list(refined.stop_before_labels)})")
            print("Review the diff, then move it into guides/ if it's an improvement.")
        except Exception as exc:
            print(f"WARNING: proposed draft failed validation: {exc}\n  left at {draft_path} for inspection")

    return 0


if __name__ == "__main__":
    sys.exit(main())
