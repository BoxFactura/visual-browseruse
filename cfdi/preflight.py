"""Pre-flight validation: every data problem we can catch before a browser opens.

Reports ALL problems at once (not first-only). The SAT catalogs are vendored
below — they change rarely; the phase-2 compiler PR owns keeping them current.
"""

import calendar
import re
from datetime import date, timedelta

from cfdi.guides import Guide

# c_RegimenFiscal (subset relevant to receptors; code → name)
REGIMEN_FISCAL = {
    "601": "General de Ley Personas Morales",
    "603": "Personas Morales con Fines no Lucrativos",
    "605": "Sueldos y Salarios e Ingresos Asimilados a Salarios",
    "606": "Arrendamiento",
    "607": "Régimen de Enajenación o Adquisición de Bienes",
    "608": "Demás ingresos",
    "610": "Residentes en el Extranjero sin Establecimiento Permanente en México",
    "611": "Ingresos por Dividendos (socios y accionistas)",
    "612": "Personas Físicas con Actividades Empresariales y Profesionales",
    "614": "Ingresos por intereses",
    "615": "Régimen de los ingresos por obtención de premios",
    "616": "Sin obligaciones fiscales",
    "620": "Sociedades Cooperativas de Producción",
    "621": "Incorporación Fiscal",
    "622": "Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras",
    "623": "Opcional para Grupos de Sociedades",
    "624": "Coordinados",
    "625": "Régimen de las Actividades Empresariales con ingresos a través de Plataformas Tecnológicas",
    "626": "Régimen Simplificado de Confianza",
}

# c_UsoCFDI subset (consumer invoicing): code → name. We do NOT pre-validate
# uso↔régimen compatibility — CFDI 4.0 / the portal validate it authoritatively
# at stamping, and a local matrix only risks wrongly blocking a valid pair.
USO_CFDI = {
    "G01": "Adquisición de mercancías",
    "G02": "Devoluciones, descuentos o bonificaciones",
    "G03": "Gastos en general",
    "D01": "Honorarios médicos, dentales y gastos hospitalarios",
    "S01": "Sin efectos fiscales",
}

RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")
CP_RE = re.compile(r"^\d{5}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def interpret_purchase_date(raw: str, today: date) -> tuple[date | None, str | None]:
    """Resolve a ticket date, tolerating extractor day/month transposition.

    A receipt describes a past purchase, so a future literal reading is
    impossible. If YYYY-MM-DD parses to a future date (or doesn't parse) and
    the day/month-swapped reading is a valid past date, the swap is the only
    consistent interpretation. Returns (date, note) — note explains a swap;
    (None, None) means unparseable either way.
    """
    def parse(year: str, month: str, day: str) -> date | None:
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            return None

    parts = str(raw).split("-")
    if len(parts) != 3:
        return None, None
    literal = parse(parts[0], parts[1], parts[2])
    swapped = parse(parts[0], parts[2], parts[1])

    if literal and literal <= today:
        return literal, None
    if swapped and swapped <= today:
        why = "the literal reading is in the future" if literal else "the literal reading is invalid"
        return swapped, f"date {raw!r} read as {swapped.isoformat()} ({why}; day/month order corrected)"
    return (literal, None) if literal else (None, None)


def invoice_cutoff(purchased: date, window_days: int | None) -> tuple[date, str]:
    """Latest date a purchase can still be self-invoiced.

    GLOBAL DEFAULT (window_days is None): the end of the purchase month — almost
    all Mexican portals close self-invoicing at month-end. A guide overrides this
    with invoicing_window.max_days_after_purchase (a rolling N-day window, e.g.
    San Pablo's 180 days).
    """
    if window_days is not None:
        return purchased + timedelta(days=window_days), f"{window_days} days after purchase"
    last_day = calendar.monthrange(purchased.year, purchased.month)[1]
    return date(purchased.year, purchased.month, last_day), "default: end of the purchase month"


def get_path(data: dict, dotted: str):
    current = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def validate_fiscal(fiscal: dict, required_fields: tuple[str, ...]) -> list[str]:
    errors = []
    for key in required_fields:
        if not fiscal.get(key):
            hint = ""
            if key == "regimen_fiscal":
                hint = ' (3-digit SAT code, e.g. "612" — it is on your Constancia de Situación Fiscal)'
            errors.append(f"fiscal data: missing required field '{key}'{hint}")

    rfc = fiscal.get("rfc")
    if rfc and not RFC_RE.fullmatch(str(rfc).upper()):
        errors.append(f"fiscal data: rfc '{rfc}' is not a valid RFC (12/13 chars: AAAA999999XXX)")
    cp = fiscal.get("cp")
    if cp and not CP_RE.fullmatch(str(cp)):
        errors.append(f"fiscal data: cp '{cp}' must be exactly 5 digits")
    email = fiscal.get("email")
    if email and not EMAIL_RE.fullmatch(str(email)):
        errors.append(f"fiscal data: email '{email}' does not look like an email address")

    regimen = fiscal.get("regimen_fiscal")
    if regimen and str(regimen) not in REGIMEN_FISCAL:
        errors.append(f"fiscal data: regimen_fiscal '{regimen}' is not in the SAT c_RegimenFiscal catalog")
    uso = fiscal.get("uso_cfdi")
    if uso and str(uso) not in USO_CFDI:
        errors.append(f"fiscal data: uso_cfdi '{uso}' is not in the vendored c_UsoCFDI catalog")
    return errors


def validate_ticket(ticket: dict, guide: Guide, today: date) -> list[str]:
    errors = []
    field_map = dict(guide.ticket_field_map)
    for dotted in guide.required_ticket_fields:
        if get_path(ticket, dotted) is None:
            errors.append(f"ticket: missing required field '{dotted}' (required by guide {guide.id})")

    total_path = field_map["total"]
    total = get_path(ticket, total_path)
    if total is not None and not (isinstance(total, (int, float)) and total > 0):
        errors.append(f"ticket: {total_path} must be a positive number, got {total!r}")

    date_path = field_map["purchase_date"]
    raw_date = get_path(ticket, date_path)
    if raw_date is not None:
        purchased, _ = interpret_purchase_date(str(raw_date), today)
        if purchased is None:
            errors.append(f"ticket: {date_path} {raw_date!r} is not a YYYY-MM-DD date")
        elif purchased > today:
            errors.append(f"ticket: {date_path} {purchased.isoformat()} is in the future")
        else:
            cutoff, desc = invoice_cutoff(purchased, guide.invoicing_window_days)
            if today > cutoff:
                errors.append(
                    f"ticket: purchase {purchased.isoformat()} is past the invoicing window "
                    f"(cutoff {cutoff.isoformat()} — {desc}). If this portal allows longer, set "
                    f"invoicing_window.max_days_after_purchase in the guide."
                )
    return errors


def preflight(ticket: dict, fiscal: dict, guide: Guide, today: date) -> list[str]:
    """All blocking problems, or an empty list when it's safe to open a browser."""
    return validate_ticket(ticket, guide, today) + validate_fiscal(fiscal, guide.required_fiscal_fields)
