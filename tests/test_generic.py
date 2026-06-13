import json
from datetime import date
from pathlib import Path

import pytest

from cfdi.guides import GENERIC_STOP_LABELS, GuideError, generic_guide
from cfdi.runner import build_task

AMORINO_TICKET = {
    "invoice": {"invoice_number": "369636", "date": "2026-11-06"},
    "summary": {"total": 195.0},
    "additional_info": {"invoice_url": "https://facturacion.amorinogelato.com"},
}
SCHEME_LESS_TICKET = {
    "issuer": {"rfc": "XXX010101000"},
    "additional_info": {"invoice_url": "www.tienda-nueva.com.mx"},
}
FISCAL = {
    "rfc": "UAP370423PP3", "nombre": "UNIVERSIDAD AUTONOMA DE PUEBLA",
    "cp": "72000", "regimen_fiscal": "603", "uso_cfdi": "G03", "email": "abc@boxfactura.com",
}


def test_generic_guide_from_amorino_ticket():
    g = generic_guide(AMORINO_TICKET)
    assert g.is_generic is True
    assert g.id == "generic-facturacion-amorinogelato-com"
    assert g.portal_url == "https://facturacion.amorinogelato.com"
    assert g.required_ticket_fields == ()
    assert g.required_fiscal_fields == ("rfc", "nombre", "cp", "regimen_fiscal", "uso_cfdi", "email")
    assert g.invoicing_window_days is None
    assert g.stop_before_labels == GENERIC_STOP_LABELS


def test_generic_guide_normalizes_scheme_less_url():
    g = generic_guide(SCHEME_LESS_TICKET)
    assert g.portal_url == "https://www.tienda-nueva.com.mx"
    assert g.id == "generic-tienda-nueva-com-mx"


def test_generic_guide_requires_a_starting_url():
    with pytest.raises(GuideError) as exc:
        generic_guide({"issuer": {"rfc": "XXX010101000"}, "additional_info": {}})
    assert str(exc.value) == (
        "no guide for this portal and the ticket has no additional_info.invoice_url "
        "to start from — add a guide or a starting URL"
    )


def test_generic_stop_labels_are_high_confidence_only():
    # bare "Generar Factura" is intentionally NOT blocked (ambiguous: entry vs final)
    assert "Generar Factura" not in GENERIC_STOP_LABELS
    assert "Timbrar" in GENERIC_STOP_LABELS
    assert "Emitir Factura" in GENERIC_STOP_LABELS


def test_generic_build_task_includes_raw_ticket_and_hints_label():
    g = generic_guide(AMORINO_TICKET)
    prompt = build_task(g, AMORINO_TICKET, FISCAL, today=date(2026, 6, 12))
    assert "# HINTS (no specific guide — adapt)" in prompt
    # default field map can't resolve Amorino's shape, so the raw ticket is appended
    assert "# TICKET (raw JSON" in prompt
    assert '"invoice_number": "369636"' in prompt
    # fiscal bundle still maps and régimen name is expanded
    assert "- {regimen_fiscal} = 603 - Personas Morales con Fines no Lucrativos" in prompt
    assert "- {uso_cfdi} = G03 - Gastos en general" in prompt


def test_matched_guide_task_has_no_raw_ticket_dump():
    from cfdi.guides import parse_guide
    g = parse_guide(Path(__file__).parent.parent / "guides" / "amorino-gelato.md")
    prompt = build_task(g, AMORINO_TICKET, FISCAL, today=date(2026, 6, 12))
    assert "# TICKET (raw JSON" not in prompt
    assert "# MERCHANT GUIDE" in prompt


def test_self_authored_draft_captures_observed_label_and_validates(tmp_path):
    from cfdi.guides import parse_guide
    from cfdi.runner import write_hint_draft

    g = generic_guide(AMORINO_TICKET)
    report = {
        "status": "ready_for_review",
        "final_url": "https://facturacion.amorinogelato.com/invoice/fiscal-data",
        "human_next_button": "GENERAR FACTURA",
        "fields_filled": "RFC = UAP370423PP3\nCódigo postal = 72000",
    }
    path = write_hint_draft(g, AMORINO_TICKET, report, tmp_path)
    assert path.name == "facturacion-amorinogelato-com.md"

    # the draft is a valid, loadable guide with the OBSERVED stop label (not a placeholder)
    drafted = parse_guide(path)
    assert drafted.stop_before_labels == ("GENERAR FACTURA",)
    assert drafted.domains == ("amorinogelato.com",)
    assert drafted.rfcs == ()  # ticket carried no issuer rfc
    assert drafted.portal_url == "https://facturacion.amorinogelato.com/invoice/fiscal-data"


def test_draft_without_observed_label_needs_human_review(tmp_path):
    from cfdi.guides import GuideError, parse_guide
    from cfdi.runner import write_hint_draft

    g = generic_guide(AMORINO_TICKET)
    path = write_hint_draft(g, AMORINO_TICKET, {"status": "ready_for_review"}, tmp_path)
    # no observed label → REVIEW_REQUIRED → the runner's loader refuses it until a human fixes it
    with pytest.raises(GuideError):
        parse_guide(path)
