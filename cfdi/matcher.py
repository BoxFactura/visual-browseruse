"""Ticket → guide matching. Deterministic tiers, never silently picks on conflict.

Tier 1: portal domain from the ticket's invoice_url (eTLD+1 via public-suffix
parsing — naive "last two labels" breaks on .com.mx). Tier 2: issuer RFC,
exact. Cross-tier disagreement is a conflict naming both candidates: on a
fiscal flow the wrong guide is worse than no guide.
"""

from dataclasses import dataclass

import tldextract

from cfdi.guides import Guide, find_invoice_url

# Offline: empty suffix_list_urls uses the bundled PSL snapshot, no network fetch.
_extract = tldextract.TLDExtract(suffix_list_urls=())


@dataclass(frozen=True)
class Signals:
    domain: str | None
    rfc: str | None


@dataclass(frozen=True)
class MatchResult:
    status: str  # 'matched' | 'no_match' | 'conflict'
    guide_id: str | None = None
    tier: str | None = None  # 'domain' | 'rfc'
    candidates: tuple[str, ...] = ()


def normalize_domain(url: str) -> str | None:
    url = url.strip().lower()
    if not url:
        return None
    parts = _extract(url)
    if not parts.domain or not parts.suffix:
        return None
    return f"{parts.domain}.{parts.suffix}"


def extract_signals(ticket: dict) -> Signals:
    invoice_url = find_invoice_url(ticket) or ""
    rfc = (ticket.get("issuer") or {}).get("rfc") or None
    return Signals(
        domain=normalize_domain(invoice_url),
        rfc=rfc.strip().upper() if rfc else None,
    )


def match(signals: Signals, guides: list[Guide]) -> MatchResult:
    by_domain = {d: g for g in guides for d in g.domains}
    by_rfc = {r: g for g in guides for r in g.rfcs}

    domain_hit = by_domain.get(signals.domain) if signals.domain else None
    rfc_hit = by_rfc.get(signals.rfc) if signals.rfc else None

    if domain_hit and rfc_hit and domain_hit.id != rfc_hit.id:
        return MatchResult(status="conflict", candidates=(domain_hit.id, rfc_hit.id))
    if domain_hit:
        return MatchResult(status="matched", guide_id=domain_hit.id, tier="domain")
    if rfc_hit:
        return MatchResult(status="matched", guide_id=rfc_hit.id, tier="rfc")
    return MatchResult(status="no_match")
