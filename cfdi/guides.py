"""Load and validate compiled invoicing guides (playbooks) from guides/*.md.

A guide is YAML frontmatter (machine-read: matching keys, gate labels, limits)
plus a markdown body (model-read: steps, quirks, error map). No index file:
with up to ~100 guides a frontmatter scan takes milliseconds, and validating
at load time removes generated-artifact sync risk.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REQUIRED_KEYS = [
    "id",
    "description",
    "match",
    "portal_url",
    "required_ticket_fields",
    "required_fiscal_fields",
    "stop",
    "patience",
    "last_verified",
]

# chars/4 ≈ tokens; real tokenizer arrives with the phase-2 compiler
BODY_BUDGET_CHARS = 20_000


class GuideError(ValueError):
    pass


@dataclass(frozen=True)
class Guide:
    id: str
    description: str
    domains: tuple[str, ...]
    rfcs: tuple[str, ...]
    portal_url: str
    required_ticket_fields: tuple[str, ...]
    required_fiscal_fields: tuple[str, ...]
    invoicing_window_days: int | None
    stop_before_labels: tuple[str, ...]
    patience_max_reload_cycles: int
    patience_wait_seconds: int
    last_verified: str
    body: str
    path: Path = field(compare=False)


def parse_guide(path: Path) -> Guide:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise GuideError(f"{path.name}: missing frontmatter opening '---'")
    _, _, rest = text.partition("---\n")
    fm_text, sep, body = rest.partition("\n---\n")
    if not sep:
        raise GuideError(f"{path.name}: missing frontmatter closing '---'")

    fm = yaml.safe_load(fm_text)
    if not isinstance(fm, dict):
        raise GuideError(f"{path.name}: frontmatter is not a mapping")

    missing = [k for k in REQUIRED_KEYS if k not in fm]
    if missing:
        raise GuideError(f"{path.name}: missing frontmatter keys: {', '.join(missing)}")

    match = fm["match"]
    domains = tuple(str(d).lower() for d in match.get("domains", []))
    rfcs = tuple(str(r).upper() for r in match.get("rfcs", []))
    if not domains and not rfcs:
        raise GuideError(f"{path.name}: match must declare at least one domain or rfc")
    for d in domains:
        if "/" in d or d.startswith("www."):
            raise GuideError(f"{path.name}: match.domains entries must be bare eTLD+1, got {d!r}")

    stop = fm["stop"]
    labels = tuple(str(s) for s in stop.get("before_labels", []))
    if not labels:
        raise GuideError(f"{path.name}: stop.before_labels must not be empty")

    window = fm.get("invoicing_window") or {}
    patience = fm["patience"]

    return Guide(
        id=str(fm["id"]),
        description=str(fm["description"]),
        domains=domains,
        rfcs=rfcs,
        portal_url=str(fm["portal_url"]),
        required_ticket_fields=tuple(fm["required_ticket_fields"]),
        required_fiscal_fields=tuple(fm["required_fiscal_fields"]),
        invoicing_window_days=window.get("max_days_after_purchase"),
        stop_before_labels=labels,
        patience_max_reload_cycles=int(patience["max_reload_cycles"]),
        patience_wait_seconds=int(patience["wait_seconds"]),
        last_verified=str(fm["last_verified"]),
        body=body.strip(),
        path=path,
    )


def load_guides(guides_dir: Path) -> list[Guide]:
    """Parse every guide, rejecting duplicate match-key claims across guides."""
    guides = [parse_guide(p) for p in sorted(guides_dir.glob("*.md"))]

    claims: dict[tuple[str, str], str] = {}
    problems: list[str] = []
    for g in guides:
        for kind, keys in (("domain", g.domains), ("rfc", g.rfcs)):
            for key in keys:
                prior = claims.setdefault((kind, key), g.id)
                if prior != g.id:
                    problems.append(f"{kind} {key!r} claimed by both {prior!r} and {g.id!r}")
        if len(g.body) > BODY_BUDGET_CHARS:
            print(f"warning: guide {g.id} body is {len(g.body)} chars (> {BODY_BUDGET_CHARS} ≈ 5k tokens)")
    if problems:
        raise GuideError("duplicate match claims: " + "; ".join(problems))
    return guides
