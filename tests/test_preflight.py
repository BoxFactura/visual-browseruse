import json
from datetime import date
from pathlib import Path

from cfdi.guides import load_guides
from cfdi.preflight import preflight, validate_fiscal, validate_ticket

FIXTURES = Path(__file__).parent / "fixtures"
POLLOS = next(g for g in load_guides(FIXTURES / "guides") if g.id == "los-pollos-hermanos")
ALL_FISCAL_FIELDS = ("rfc", "nombre", "cp", "regimen_fiscal", "uso_cfdi", "email")

GOOD_FISCAL = {
    "rfc": "GOFG680425NF8",
    "nombre": "Gustavo Fring",
    "cp": "04030",
    "regimen_fiscal": "612",
    "uso_cfdi": "G03",
    "email": "gus@lospollos.mx",
}


def pollos_ticket() -> dict:
    return json.loads((FIXTURES / "tickets" / "los_pollos_hermanos.json").read_text(encoding="utf-8"))


def test_good_fiscal_and_ticket_pass():
    assert preflight(pollos_ticket(), GOOD_FISCAL, POLLOS, today=date(2026, 6, 12)) == []


def test_missing_regimen_fiscal_named_exactly():
    fiscal = {k: v for k, v in GOOD_FISCAL.items() if k != "regimen_fiscal"}
    assert validate_fiscal(fiscal, ALL_FISCAL_FIELDS) == [
        "fiscal data: missing required field 'regimen_fiscal' "
        '(3-digit SAT code, e.g. "612" — it is on your Constancia de Situación Fiscal)'
    ]


def test_uso_regimen_compatibility_not_pre_validated():
    # SAT / the portal validate uso↔régimen at stamping; we do NOT pre-block it
    # locally (a local matrix only risks wrongly rejecting a valid pair).
    assert validate_fiscal(GOOD_FISCAL | {"regimen_fiscal": "605"}, ALL_FISCAL_FIELDS) == []
    assert validate_fiscal(GOOD_FISCAL | {"regimen_fiscal": "616"}, ALL_FISCAL_FIELDS) == []


def test_g03_with_612_passes():
    assert validate_fiscal(GOOD_FISCAL, ALL_FISCAL_FIELDS) == []


def test_uso_cfdi_ordered_list_validated():
    # a list of valid usos passes; an unknown code in the list is flagged by name
    assert validate_fiscal(GOOD_FISCAL | {"uso_cfdi": ["G03", "D01"]}, ALL_FISCAL_FIELDS) == []
    assert validate_fiscal(GOOD_FISCAL | {"uso_cfdi": ["G03", "Z99"]}, ALL_FISCAL_FIELDS) == [
        "fiscal data: uso_cfdi 'Z99' is not in the vendored c_UsoCFDI catalog"
    ]


def test_bad_rfc_cp_email_all_reported_at_once():
    fiscal = GOOD_FISCAL | {"rfc": "NOPE", "cp": "123", "email": "not-an-email"}
    assert validate_fiscal(fiscal, ALL_FISCAL_FIELDS) == [
        "fiscal data: rfc 'NOPE' is not a valid RFC (12/13 chars: AAAA999999XXX)",
        "fiscal data: cp '123' must be exactly 5 digits",
        "fiscal data: email 'not-an-email' does not look like an email address",
    ]


def test_unknown_codes_rejected():
    fiscal = GOOD_FISCAL | {"regimen_fiscal": "999", "uso_cfdi": "Z99"}
    assert validate_fiscal(fiscal, ALL_FISCAL_FIELDS) == [
        "fiscal data: regimen_fiscal '999' is not in the SAT c_RegimenFiscal catalog",
        "fiscal data: uso_cfdi 'Z99' is not in the vendored c_UsoCFDI catalog",
    ]


def test_ticket_missing_required_field():
    ticket = pollos_ticket()
    del ticket["invoice_data"]["facturacion_folio"]
    assert validate_ticket(ticket, POLLOS, today=date(2026, 6, 12)) == [
        "ticket: missing required field 'invoice_data.facturacion_folio' "
        "(required by guide los-pollos-hermanos)"
    ]


def test_ticket_outside_invoicing_window():
    # los-pollos overrides with a rolling 30-day window
    ticket = pollos_ticket()
    ticket["purchase"]["date"] = "2026-01-05"
    assert validate_ticket(ticket, POLLOS, today=date(2026, 6, 12)) == [
        "ticket: purchase 2026-01-05 is past the invoicing window (cutoff 2026-02-04 — "
        "30 days after purchase). If this portal allows longer, set "
        "invoicing_window.max_days_after_purchase in the guide."
    ]


def _lean_guide(tmp_path):
    from cfdi.guides import parse_guide

    p = tmp_path / "lean.md"
    p.write_text(
        "---\nid: lean\ndescription: d\nmatch:\n  domains: [lean.com]\n"
        "portal_url: https://lean.com/\n---\nhint\n",
        encoding="utf-8",
    )
    return parse_guide(p)


def test_global_default_window_is_end_of_purchase_month(tmp_path):
    # a guide with no invoicing_window → global default: end of the purchase month
    guide = _lean_guide(tmp_path)
    last_month = {"purchase": {"date": "2026-05-20", "total": 100.0}}
    assert validate_ticket(last_month, guide, today=date(2026, 6, 12)) == [
        "ticket: purchase 2026-05-20 is past the invoicing window (cutoff 2026-05-31 — "
        "default: end of the purchase month). If this portal allows longer, set "
        "invoicing_window.max_days_after_purchase in the guide."
    ]


def test_global_default_window_same_month_passes(tmp_path):
    guide = _lean_guide(tmp_path)
    same_month = {"purchase": {"date": "2026-06-09", "total": 100.0}}
    assert validate_ticket(same_month, guide, today=date(2026, 6, 12)) == []


def test_interpret_purchase_date_table():
    from cfdi.preflight import interpret_purchase_date

    assert interpret_purchase_date("2026-11-06", date(2026, 6, 12)) == (
        date(2026, 6, 11),
        "date '2026-11-06' read as 2026-06-11 (the literal reading is in the future; "
        "day/month order corrected)",
    )
    assert interpret_purchase_date("2026-04-05", date(2026, 6, 12)) == (date(2026, 4, 5), None)
    assert interpret_purchase_date("2026-13-01", date(2026, 6, 12)) == (
        date(2026, 1, 13),
        "date '2026-13-01' read as 2026-01-13 (the literal reading is invalid; "
        "day/month order corrected)",
    )
    assert interpret_purchase_date("2026-12-25", date(2026, 6, 12)) == (date(2026, 12, 25), None)
    assert interpret_purchase_date("garbage", date(2026, 6, 12)) == (None, None)


def test_amorino_shaped_ticket_passes_with_field_map():
    from cfdi.guides import parse_guide

    amorino = parse_guide(Path(__file__).parent.parent / "guides" / "amorino-gelato.md")
    ticket = {
        "invoice": {"invoice_number": "369636", "date": "2026-11-06"},
        "summary": {"total": 195.0},
        "additional_info": {"invoice_url": "https://facturacion.amorinogelato.com"},
    }
    assert validate_ticket(ticket, amorino, today=date(2026, 6, 12)) == []


def test_ticket_future_date_and_zero_total():
    ticket = pollos_ticket()
    ticket["purchase"]["date"] = "2027-01-01"
    ticket["purchase"]["total"] = 0
    assert validate_ticket(ticket, POLLOS, today=date(2026, 6, 12)) == [
        "ticket: purchase.total must be a positive number, got 0",
        "ticket: purchase.date 2027-01-01 is in the future",
    ]
