"""Load and validate compiled invoicing guides (playbooks) from guides/*.md.

A guide is YAML frontmatter (machine-read: matching keys, gate labels, limits)
plus a markdown body (model-read: steps, quirks, error map). No index file:
with up to ~100 guides a frontmatter scan takes milliseconds, and validating
at load time removes generated-artifact sync risk.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# A guide can be just a few hints: only the essentials are required; the rest
# default so a new guide is id + description + match + portal_url + stop + body.
REQUIRED_KEYS = ["id", "description", "match", "portal_url"]

# Universal receptor bundle — the default required_fiscal_fields for any guide.
DEFAULT_FISCAL_FIELDS = ("rfc", "nombre", "cp", "regimen_fiscal", "uso_cfdi", "email")

# chars/4 ≈ tokens; real tokenizer arrives with the phase-2 compiler
BODY_BUDGET_CHARS = 20_000

# Canonical placeholder → default ticket-JSON path. A guide overrides entries via
# frontmatter `ticket_field_map` when its merchant's extractor emits another shape.
DEFAULT_TICKET_FIELD_MAP = {
    "facturacion_folio": "invoice_data.facturacion_folio",
    "total": "purchase.total",
    "purchase_date": "purchase.date",
}


def normalize_store_name(name: str) -> str:
    """Casefold + collapse whitespace so a ticket's facturadata.store_name and a
    guide's declared match.names compare reliably ('Bodegas  Alianza' →
    'bodegas alianza'). Empty / whitespace-only → ''."""
    return " ".join(str(name).split()).casefold()


class GuideError(ValueError):
    pass


@dataclass(frozen=True)
class Guide:
    id: str
    description: str
    domains: tuple[str, ...]
    rfcs: tuple[str, ...]
    names: tuple[str, ...]  # normalized store names (match against facturadata.store_name)
    portal_url: str
    required_ticket_fields: tuple[str, ...]
    required_fiscal_fields: tuple[str, ...]
    invoicing_window_days: int | None
    ticket_field_map: tuple[tuple[str, str], ...]
    stop_before_labels: tuple[str, ...]
    patience_max_reload_cycles: int
    patience_wait_seconds: int
    last_verified: str
    body: str
    path: Path = field(compare=False)
    is_generic: bool = field(default=False, compare=False)  # synthesized for an unknown portal


# Portable hints that apply to ~any Mexican CFDI portal — the distilled
# cross-portal knowledge an unknown-portal run starts from (no per-site steps).
GENERIC_HINTS_BODY = """\
## Unknown portal — adapt to the live page
There is no portal-specific guide yet. These are general hints, not a script:
read the screen and adapt.

## Approach
1. You start at the ticket's invoicing URL. If it is a marketing homepage, find the
   "Facturación" / "Factura" / "Facturar" entry and open it. A blank page is usually a
   slow SPA — wait and reload (see patience limits) before concluding it is down.
2. Forms are usually 1-3 steps: ticket lookup (número/folio; sometimes total and date),
   then receptor fiscal data, then an emit button. Fill only the fields THIS portal shows
   from the data below — portals ask for a SUBSET. Never invent a value you were not given.
3. Bad-design flow is common: you often must fill a field and THEN click a button
   (Buscar / Siguiente / Continuar) to advance or to enable later fields. After filling,
   look for and click that advance button.
4. Régimen fiscal and Uso de CFDI are dropdowns — sometimes native <select>, sometimes
   custom click-to-open lists. Select the option whose TEXT matches the name after the
   code in the data, then verify it shows that name.
5. Some fields carry over between steps (often RFC) — verify, do not re-type.

## Quirks
| symptom | workaround |
|---|---|
| typed value doubles or won't stick (React/Vue input) | set the value once via the native setter + input/change events; don't retype char-by-char |
| amount field is currency-masked / mangled | use type_slowly with the exact value (e.g. "2306.00"), then verify |
| custom dropdown won't accept a typed value | click it to open, then click the option by its visible name |
| page blank after navigation | slow SPA: wait, reload, up to the patience limit; never declare it down first |

## Error codes
| portal message contains | meaning | action |
|---|---|---|
| ya facturado / previamente facturado | this ticket already has an invoice | abort with status already_invoiced |
| CFDI40147 / domicilio fiscal / no coincide con el SAT | receptor RFC/name/CP/régimen ≠ SAT registry | abort aborted_error_code: tell the user to match their Constancia de Situación Fiscal |
| problema técnico / intente más tarde / error al generar | portal-side failure | abort aborted_error_code |

## Stop & completion
First encounter with this portal — a human verifies. NEVER click a final emit button
(labels containing Emitir / Generar / Timbrar / Facturar / Enviar at the last step).
When the form is filled and only that button remains, call ready_for_review with the
EXACT label of that button — that is what teaches the portal's real stop label."""

# High-confidence final-emit verbs the click-guard blocks by default — for unknown
# portals AND any guide that doesn't declare its own stop.before_labels. The agent
# is told to recognize the final-submit button itself (its policy); this is the
# mechanical safety net under that judgment. Bare "Generar Factura" is deliberately
# excluded — it's the ENTRY button on some portals (San Pablo) and the FINAL button
# on others (Amorino), so it can't be blocked blindly; a guide adds the exact label
# only when it wants precision (e.g. for unattended auto-submit).
GENERIC_STOP_LABELS = (
    "Timbrar", "Emitir Factura", "Emitir CFDI", "Generar CFDI",
    "Generar Factura y Enviar", "Generar y Enviar", "Enviar Factura", "Facturar y Enviar",
)


def _host_slug(url: str) -> str:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return host.replace(".", "-") or "unknown-portal"


_URL_HINT_WORDS = ("factura", "invoice", "cfdi", "portal")
_URLISH = re.compile(r"^(https?://)?(www\.)?([a-z0-9-]+\.)+[a-z]{2,}(/[^\s]*)?$", re.I)


def _walk_strings(obj, keypath=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{keypath}.{k}" if keypath else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{keypath}[{i}]")
    elif isinstance(obj, str):
        yield keypath, obj


def find_invoice_url(ticket: dict) -> str | None:
    """Most likely invoicing-portal URL in a ticket, normalized to https.

    Extractors put the URL under inconsistent keys (additional_info.invoice_url,
    facturacion.url, ...), so compile EVERY url-like value and score by invoicing
    hints in the key path and host rather than trust one key. Returns None if the
    ticket carries no url-like value at all.
    """
    best, best_score = None, -1
    for keypath, raw in _walk_strings(ticket):
        s = raw.strip()
        if not s or " " in s or "@" in s or not _URLISH.match(s):
            continue
        host = s.split("//", 1)[-1].split("/", 1)[0].lower()
        score = 0
        if any(w in keypath.lower() for w in _URL_HINT_WORDS):
            score += 3
        if any(w in host for w in _URL_HINT_WORDS):
            score += 2
        if s.lower().startswith("http"):
            score += 1
        if score > best_score:
            best, best_score = s, score
    if best is None:
        return None
    return best if best.lower().startswith(("http://", "https://")) else "https://" + best


def generic_guide(ticket: dict) -> Guide:
    """Synthesize an adaptive hint-guide for a portal we have no guide for.

    Raises GuideError if the ticket carries no invoicing URL to start from.
    """
    invoice_url = find_invoice_url(ticket)
    if not invoice_url:
        raise GuideError(
            "no guide for this portal and no invoicing URL found in the ticket "
            "— add a guide or a starting URL"
        )

    return Guide(
        id=f"generic-{_host_slug(invoice_url)}",
        description="Adaptive hint-guide for an unknown CFDI portal (no specific guide yet).",
        domains=(),
        rfcs=(),
        names=(),
        portal_url=invoice_url,
        required_ticket_fields=(),  # adapt to whatever the portal asks
        required_fiscal_fields=("rfc", "nombre", "cp", "regimen_fiscal", "uso_cfdi", "email"),
        invoicing_window_days=None,
        ticket_field_map=tuple(sorted(DEFAULT_TICKET_FIELD_MAP.items())),
        stop_before_labels=GENERIC_STOP_LABELS,
        patience_max_reload_cycles=3,
        patience_wait_seconds=10,
        last_verified="never",
        body=GENERIC_HINTS_BODY,
        path=Path("<generic>"),
        is_generic=True,
    )


def parse_guide(path: Path, allow_review_placeholder: bool = False) -> Guide:
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
    names = tuple(n for n in (normalize_store_name(x) for x in match.get("names", [])) if n)
    if not domains and not rfcs and not names:
        raise GuideError(f"{path.name}: match must declare at least one domain, rfc, or name")
    for d in domains:
        if "/" in d or d.startswith("www."):
            raise GuideError(f"{path.name}: match.domains entries must be bare eTLD+1, got {d!r}")

    # stop is optional: omit it and the agent figures out the final-submit button
    # (per its policy), with the default emit-verb set as the mechanical safety net.
    stop = fm.get("stop") or {}
    labels = tuple(str(s) for s in stop.get("before_labels", [])) or GENERIC_STOP_LABELS
    if "REVIEW_REQUIRED" in labels and not allow_review_placeholder:
        raise GuideError(
            f"{path.name}: stop.before_labels contains the compiler placeholder "
            f"REVIEW_REQUIRED — a human must confirm the real final-submit label first"
        )

    window = fm.get("invoicing_window") or {}
    patience = fm.get("patience") or {}

    field_map = dict(DEFAULT_TICKET_FIELD_MAP)
    overrides = fm.get("ticket_field_map") or {}
    unknown = sorted(set(overrides) - set(DEFAULT_TICKET_FIELD_MAP))
    if unknown:
        raise GuideError(
            f"{path.name}: ticket_field_map has unknown placeholders: {', '.join(unknown)} "
            f"(known: {', '.join(sorted(DEFAULT_TICKET_FIELD_MAP))})"
        )
    field_map.update({k: str(v) for k, v in overrides.items()})

    return Guide(
        id=str(fm["id"]),
        description=str(fm["description"]),
        domains=domains,
        rfcs=rfcs,
        names=names,
        portal_url=str(fm["portal_url"]),
        required_ticket_fields=tuple(fm.get("required_ticket_fields") or ()),
        required_fiscal_fields=tuple(fm.get("required_fiscal_fields") or DEFAULT_FISCAL_FIELDS),
        invoicing_window_days=window.get("max_days_after_purchase"),
        ticket_field_map=tuple(sorted(field_map.items())),
        stop_before_labels=labels,
        patience_max_reload_cycles=int(patience.get("max_reload_cycles", 3)),
        patience_wait_seconds=int(patience.get("wait_seconds", 10)),
        last_verified=str(fm.get("last_verified", "never")),
        body=body.strip(),
        path=path,
    )


def load_guides(guides_dir: Path) -> list[Guide]:
    """Parse every guide, rejecting duplicate match-key claims across guides."""
    guides = [parse_guide(p) for p in sorted(guides_dir.glob("*.md"))]

    claims: dict[tuple[str, str], str] = {}
    problems: list[str] = []
    for g in guides:
        for kind, keys in (("domain", g.domains), ("rfc", g.rfcs), ("name", g.names)):
            for key in keys:
                prior = claims.setdefault((kind, key), g.id)
                if prior != g.id:
                    problems.append(f"{kind} {key!r} claimed by both {prior!r} and {g.id!r}")
        if len(g.body) > BODY_BUDGET_CHARS:
            print(f"warning: guide {g.id} body is {len(g.body)} chars (> {BODY_BUDGET_CHARS} ≈ 5k tokens)")
    if problems:
        raise GuideError("duplicate match claims: " + "; ".join(problems))
    return guides
