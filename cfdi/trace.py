"""Capture a structured per-step trace of an agent run, for guide refinement.

A dry run (supervised, no submit) records what the agent actually did at each
step — its read of the previous action, its next goal, and the actions taken —
so we can see where it stumbles (wasted waits, reloads, dropdown fumbles,
re-entering already-filled fields) and tighten the guide to cut steps.
"""

import json
from pathlib import Path

# eval-verdict words that mark a step as friction rather than clean progress
_FRICTION_WORDS = (
    "fracaso", "failed", "failure", "bloqueo", "no se", "uncertain", "incierto",
    "parcial", "partial", "error", "vac", "blank", "reintent", "retry", "no pude",
)
_WASTED_ACTIONS = {"wait", "scroll", "navigate"}


class StepTrace:
    def __init__(self) -> None:
        self.steps: list[dict] = []

    async def callback(self, browser_state_summary, model_output, step: int) -> None:
        actions = []
        for action in model_output.action:
            dumped = action.model_dump(exclude_none=True)
            if not dumped:
                continue
            name, params = next(iter(dumped.items()))
            summary = {
                k: params[k]
                for k in ("index", "url", "seconds", "text", "digits")
                if isinstance(params, dict) and k in params
            }
            actions.append({"name": name, **summary})
        self.steps.append({
            "step": step,
            "url": getattr(browser_state_summary, "url", None),
            "eval": (model_output.evaluation_previous_goal or "").strip(),
            "goal": (model_output.next_goal or "").strip(),
            "actions": actions,
        })

    def friction_summary(self) -> dict:
        counts: dict[str, int] = {}
        friction_steps = []
        for row in self.steps:
            for a in row["actions"]:
                counts[a["name"]] = counts.get(a["name"], 0) + 1
            verdict = row["eval"].lower()
            if any(w in verdict for w in _FRICTION_WORDS):
                friction_steps.append(row["step"])
        wasted = sum(counts.get(a, 0) for a in _WASTED_ACTIONS)
        return {
            "total_steps": len(self.steps),
            "action_counts": counts,
            "wasted_action_count": wasted,
            "friction_steps": friction_steps,
        }

    def render(self) -> str:
        lines = []
        for row in self.steps:
            acts = ", ".join(
                a["name"] + (f"#{a['index']}" if "index" in a else "")
                for a in row["actions"]
            )
            lines.append(f"[{row['step']}] {acts or '(no action)'}")
            if row["eval"]:
                lines.append(f"      eval: {row['eval']}")
            if row["goal"]:
                lines.append(f"      goal: {row['goal']}")
        return "\n".join(lines)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in self.steps:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
