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
        "fiscal.json: missing required field 'regimen_fiscal' "
        '(3-digit SAT code, e.g. "612" — it is on your Constancia de Situación Fiscal)'
    ]


def test_g03_with_605_rejected_naming_valid_usos():
    fiscal = GOOD_FISCAL | {"regimen_fiscal": "605"}
    assert validate_fiscal(fiscal, ALL_FISCAL_FIELDS) == [
        "fiscal.json: uso_cfdi G03 (Gastos en general) is not SAT-valid for régimen 605 "
        "(Sueldos y Salarios e Ingresos Asimilados a Salarios); valid usos for your régimen: D01, S01"
    ]


def test_g03_with_616_rejected():
    fiscal = GOOD_FISCAL | {"regimen_fiscal": "616"}
    assert validate_fiscal(fiscal, ALL_FISCAL_FIELDS) == [
        "fiscal.json: uso_cfdi G03 (Gastos en general) is not SAT-valid for régimen 616 "
        "(Sin obligaciones fiscales); valid usos for your régimen: S01"
    ]


def test_g03_with_612_passes():
    assert validate_fiscal(GOOD_FISCAL, ALL_FISCAL_FIELDS) == []


def test_bad_rfc_cp_email_all_reported_at_once():
    fiscal = GOOD_FISCAL | {"rfc": "NOPE", "cp": "123", "email": "not-an-email"}
    assert validate_fiscal(fiscal, ALL_FISCAL_FIELDS) == [
        "fiscal.json: rfc 'NOPE' is not a valid RFC (12/13 chars: AAAA999999XXX)",
        "fiscal.json: cp '123' must be exactly 5 digits",
        "fiscal.json: email 'not-an-email' does not look like an email address",
    ]


def test_unknown_codes_rejected():
    fiscal = GOOD_FISCAL | {"regimen_fiscal": "999", "uso_cfdi": "Z99"}
    assert validate_fiscal(fiscal, ALL_FISCAL_FIELDS) == [
        "fiscal.json: regimen_fiscal '999' is not in the SAT c_RegimenFiscal catalog",
        "fiscal.json: uso_cfdi 'Z99' is not in the vendored c_UsoCFDI catalog",
    ]


def test_ticket_missing_required_field():
    ticket = pollos_ticket()
    del ticket["invoice_data"]["facturacion_folio"]
    assert validate_ticket(ticket, POLLOS, today=date(2026, 6, 12)) == [
        "ticket: missing required field 'invoice_data.facturacion_folio' "
        "(required by guide los-pollos-hermanos)"
    ]


def test_ticket_outside_invoicing_window():
    ticket = pollos_ticket()
    ticket["purchase"]["date"] = "2026-01-05"
    assert validate_ticket(ticket, POLLOS, today=date(2026, 6, 12)) == [
        "ticket: purchase is 158 days old; guide los-pollos-hermanos allows invoicing within 30 days"
    ]


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
