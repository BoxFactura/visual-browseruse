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


def test_generic_guide_requires_a_url_or_store_name():
    # neither an invoicing URL nor a facturadata.store_name → nothing to start from
    with pytest.raises(GuideError) as exc:
        generic_guide({"issuer": {"rfc": "XXX010101000"}, "additional_info": {}})
    assert str(exc.value) == (
        "no guide for this portal, no invoicing URL in the ticket, and no "
        "facturadata.store_name to search — add a guide or a starting URL"
    )


def test_generic_guide_searches_when_no_url_but_store_name():
    # no URL anywhere, but facturadata names the store → start on a web search and
    # let the agent find the merchant's official portal
    g = generic_guide({"facturadata": {"store_name": "Bodegas Alianza"}, "totals": {"total": 104.5}})
    assert g.is_generic is True
    assert g.id == "generic-bodegas-alianza"
    assert g.portal_url == "https://www.google.com/search?q=Bodegas+Alianza+facturaci%C3%B3n+CFDI"
    assert g.stop_before_labels == GENERIC_STOP_LABELS
    assert "start on a web search" in g.body
    assert "prefer the merchant's own domain" in g.body


# Real "Tiendas Extra" ticket shape: URL at facturacion.url, not the assumed key.
EXTRA_TICKET = {
    "store": {"name": "Tiendas Extra", "tax_id": "TEX9302097F3"},
    "document": {"date": "2026-06-09"},
    "totals": {"total": 126.0},
    "facturacion": {"url": "https://facturacion.portalcsk.com/", "folio": "1279724"},
    "raw_notes": ["Ticket heavily wrinkled; some values may be inaccurate."],
}


def test_find_invoice_url_scans_inconsistent_keys():
    from cfdi.guides import find_invoice_url

    assert find_invoice_url(EXTRA_TICKET) == "https://facturacion.portalcsk.com/"
    assert find_invoice_url(AMORINO_TICKET) == "https://facturacion.amorinogelato.com"
    assert find_invoice_url({"additional_info": {"invoice_url": "www.farmaciasanpablo.com.mx"}}) == \
        "https://www.farmaciasanpablo.com.mx"
    # no url-like value anywhere → None (prose, ids, amounts are not URLs)
    assert find_invoice_url({"store": {"address": "Zavaleta 5567 Local 2"}, "total": "126.00"}) is None


def test_find_invoice_url_prefers_invoicing_hint_over_other_urls():
    from cfdi.guides import find_invoice_url

    ticket = {
        "store": {"website": "https://www.tiendasextra.com"},
        "facturacion": {"url": "https://facturacion.portalcsk.com/"},
    }
    assert find_invoice_url(ticket) == "https://facturacion.portalcsk.com/"


def test_generic_guide_on_extra_ticket_shape():
    g = generic_guide(EXTRA_TICKET)
    assert g.portal_url == "https://facturacion.portalcsk.com/"
    assert g.id == "generic-facturacion-portalcsk-com"


def test_matcher_signals_use_robust_url():
    from cfdi.matcher import extract_signals, Signals

    # URL under facturacion.url is still found for the domain signal
    assert extract_signals(EXTRA_TICKET) == Signals(domain="portalcsk.com", rfc=None)


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


def test_matched_lean_guide_gets_raw_ticket_when_map_unresolved(tmp_path):
    # A lean guide (default field map) on a foreign-shape ticket: the mapped values
    # don't resolve, so the agent is handed the raw ticket — no per-portal map needed.
    from cfdi.guides import parse_guide

    (tmp_path / "lean.md").write_text(
        "---\nid: tiendas-extra\ndescription: d\nmatch:\n  domains: [portalcsk.com]\n"
        "portal_url: https://facturacion.portalcsk.com/\n---\n- a hint\n",
        encoding="utf-8",
    )
    g = parse_guide(tmp_path / "lean.md")
    prompt = build_task(g, EXTRA_TICKET, FISCAL, today=date(2026, 6, 12))
    assert "# TICKET (raw JSON" in prompt
    assert '"folio": "1279724"' in prompt


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
