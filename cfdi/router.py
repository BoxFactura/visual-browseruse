"""Agent-driven guide matching.

Ticket shapes never standardize, so we don't key off fixed fields. An LLM reads
the whole ticket, identifies the merchant/store (its name can be under any key),
and picks the hint guide for that same merchant — or none, in which case the
caller goes with the ticket's invoicing URL if there is one.
"""

import json

from cfdi.guides import Guide

ROUTER_PROMPT = """\
You match a Mexican purchase ticket to an invoicing hint guide.

Given the ticket JSON and a catalog of guides, identify the ticket's MERCHANT /
store — its name, brand, legal name, RFC, or invoicing domain may appear under
any key, in any shape. Return the id of the guide that belongs to that SAME
merchant. Match on merchant identity, never on coincidental shared words. If no
guide clearly belongs to this merchant, return "none".

Reply with ONLY the guide id, or none. No other text."""


def _pick_id(answer: str, guides: list[Guide]) -> str | None:
    """Validate the model's answer against the real guide ids (defensive)."""
    answer = (answer or "").strip().strip('".​').strip().lower()
    by_id = {g.id.lower(): g.id for g in guides}
    return by_id.get(answer)


def route_guide(ticket: dict, guides: list[Guide], model: str) -> str | None:
    """Return the matching guide id, or None (→ caller uses the ticket URL)."""
    if not guides:
        return None
    from openai import OpenAI

    catalog = [
        {"id": g.id, "description": g.description,
         "domains": list(g.domains), "rfcs": list(g.rfcs)}
        for g in guides
    ]
    user = (
        "TICKET:\n" + json.dumps(ticket, ensure_ascii=False)
        + "\n\nGUIDES:\n" + json.dumps(catalog, ensure_ascii=False)
    )
    response = OpenAI().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": user},
        ],
    )
    return _pick_id(response.choices[0].message.content, guides)
