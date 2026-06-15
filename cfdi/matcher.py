"""Ticket → guide matching. Deterministic tiers, never silently picks on conflict.

The ticket's top-level `facturadata` block is the standardized matching input:
its `store_name` (matched against a guide's match.names) and optional `rfc`
(against match.rfcs). We also derive a portal domain from any invoice_url in the
ticket (eTLD+1 via public-suffix parsing — naive "last two labels" breaks on
.com.mx) and fall back to issuer.rfc when facturadata carries no rfc. When more
than one signal hits different guides it's a conflict naming every candidate: on
a fiscal flow the wrong guide is worse than no guide. Everything missing → the
caller falls back to the LLM router, then the ticket's own URL.
"""

from dataclasses import dataclass

import tldextract

from cfdi.guides import Guide, find_invoice_url, normalize_store_name

# Offline: empty suffix_list_urls uses the bundled PSL snapshot, no network fetch.
_extract = tldextract.TLDExtract(suffix_list_urls=())


@dataclass(frozen=True)
class Signals:
    domain: str | None
    rfc: str | None
    store_name: str | None = None


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
    facturadata = ticket.get("facturadata") or {}
    store_name = normalize_store_name(facturadata.get("store_name") or "") or None
    rfc = facturadata.get("rfc") or (ticket.get("issuer") or {}).get("rfc") or None
    invoice_url = find_invoice_url(ticket) or ""
    return Signals(
        domain=normalize_domain(invoice_url),
        rfc=rfc.strip().upper() if rfc else None,
        store_name=store_name,
    )


def match(signals: Signals, guides: list[Guide]) -> MatchResult:
    by_domain = {d: g for g in guides for d in g.domains}
    by_rfc = {r: g for g in guides for r in g.rfcs}
    by_name = {n: g for g in guides for n in g.names}

    # Collect every signal that hits a guide, most-precise first (RFC is an exact
    # fiscal id, store name a canonical label, domain an incidental URL host).
    hits: list[tuple[str, Guide]] = []
    if signals.rfc and signals.rfc in by_rfc:
        hits.append(("rfc", by_rfc[signals.rfc]))
    if signals.store_name and signals.store_name in by_name:
        hits.append(("name", by_name[signals.store_name]))
    if signals.domain and signals.domain in by_domain:
        hits.append(("domain", by_domain[signals.domain]))

    candidates = tuple(sorted({g.id for _, g in hits}))
    if len(candidates) > 1:
        return MatchResult(status="conflict", candidates=candidates)
    if hits:
        tier, guide = hits[0]
        return MatchResult(status="matched", guide_id=guide.id, tier=tier)
    return MatchResult(status="no_match")
