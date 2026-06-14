from pathlib import Path

import pytest

from cfdi.guides import Guide, GuideError, load_guides, parse_guide

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parent.parent


def test_parse_fixture_guide_full_object():
    guide = parse_guide(FIXTURES / "guides" / "los-pollos-hermanos.md")
    assert guide == Guide(
        id="los-pollos-hermanos",
        description="CFDI invoicing for Los Pollos Hermanos restaurant tickets.",
        domains=("lospolloshermanos.com.mx",),
        rfcs=("PHE850315GH7",),
        portal_url="https://factura.lospolloshermanos.com.mx/",
        required_ticket_fields=("invoice_data.facturacion_folio", "purchase.total"),
        required_fiscal_fields=("rfc", "nombre", "cp", "regimen_fiscal", "uso_cfdi", "email"),
        invoicing_window_days=30,
        ticket_field_map=(
            ("facturacion_folio", "invoice_data.facturacion_folio"),
            ("purchase_date", "purchase.date"),
            ("total", "purchase.total"),
        ),
        stop_before_labels=("Facturar",),
        patience_max_reload_cycles=2,
        patience_wait_seconds=5,
        last_verified="2026-06-01",
        body='## Steps\n1. Enter the folio and click "Buscar".',
        path=FIXTURES / "guides" / "los-pollos-hermanos.md",
    )


def test_parse_real_san_pablo_guide():
    guide = parse_guide(REPO / "guides" / "farmacia-san-pablo.md")
    assert guide.id == "farmacia-san-pablo"
    assert guide.domains == ("farmaciasanpablo.com.mx",)
    assert guide.rfcs == ("PPL961114GZ1",)
    assert guide.portal_url == "https://emision-sanpablo-portal-auto-prod.pegasotecnologia.mx/"
    assert guide.stop_before_labels == ("Emitir Factura", "Generar Factura y Enviar")
    assert guide.invoicing_window_days == 180
    assert guide.patience_max_reload_cycles == 3
    assert "type_slowly" in guide.body
    assert "CFDI40147" in guide.body
    assert "NEVER click it" in guide.body


def test_load_guides_returns_both_fixtures():
    guides = load_guides(FIXTURES / "guides")
    assert [g.id for g in guides] == ["los-pollos-hermanos", "madrigal-electromotive"]


def test_real_amorino_guide_ticket_field_map():
    guide = parse_guide(REPO / "guides" / "amorino-gelato.md")
    assert guide.ticket_field_map == (
        ("facturacion_folio", "invoice.invoice_number"),
        ("purchase_date", "invoice.date"),
        ("total", "summary.total"),
    )
    assert guide.required_ticket_fields == ()  # leaned: agent reads the ticket, no hard-required fields
    assert guide.stop_before_labels == ("GENERAR FACTURA",)


def test_unknown_ticket_field_map_placeholder_rejected(tmp_path):
    text = (FIXTURES / "guides" / "los-pollos-hermanos.md").read_text(encoding="utf-8")
    (tmp_path / "weird.md").write_text(
        text.replace(
            "patience: { max_reload_cycles: 2, wait_seconds: 5 }",
            "patience: { max_reload_cycles: 2, wait_seconds: 5 }\nticket_field_map: { folio: a.b }",
        ),
        encoding="utf-8",
    )
    with pytest.raises(GuideError) as exc:
        parse_guide(tmp_path / "weird.md")
    assert str(exc.value) == (
        "weird.md: ticket_field_map has unknown placeholders: folio "
        "(known: facturacion_folio, purchase_date, total)"
    )


def test_duplicate_match_claims_rejected():
    with pytest.raises(GuideError) as exc:
        load_guides(FIXTURES / "guides_dup")
    assert str(exc.value) == (
        "duplicate match claims: rfc 'PHE850315GH7' claimed by both 'pollos-clone' and 'vamonos-pest'"
    )


def test_missing_keys_rejected(tmp_path):
    (tmp_path / "bad.md").write_text("---\nid: bad\n---\nbody\n", encoding="utf-8")
    with pytest.raises(GuideError) as exc:
        parse_guide(tmp_path / "bad.md")
    assert str(exc.value) == (
        "bad.md: missing frontmatter keys: description, match, portal_url"
    )


def test_minimal_guide_just_hints(tmp_path):
    # Leanest guide: id + description + match + portal_url + a few hints. No stop —
    # the agent figures out the final-submit button; defaults fill everything else.
    from cfdi.guides import GENERIC_STOP_LABELS

    (tmp_path / "min.md").write_text(
        "---\n"
        "id: tiendas-extra\n"
        "description: CFDI invoicing for Tiendas Extra.\n"
        "match:\n  domains: [portalcsk.com]\n"
        "portal_url: https://facturacion.portalcsk.com/\n"
        "---\n"
        "- the total field has a mask: type digits only\n"
        "- after RFC, click \"Buscar cliente\" to autofill\n",
        encoding="utf-8",
    )
    g = parse_guide(tmp_path / "min.md")
    assert g.id == "tiendas-extra"
    assert g.domains == ("portalcsk.com",)
    assert g.portal_url == "https://facturacion.portalcsk.com/"
    # no stop declared → falls back to the default emit-verb safety net
    assert g.stop_before_labels == GENERIC_STOP_LABELS
    # defaults applied
    assert g.required_ticket_fields == ()
    assert g.required_fiscal_fields == ("rfc", "nombre", "cp", "regimen_fiscal", "uso_cfdi", "email")
    assert g.patience_max_reload_cycles == 3
    assert g.patience_wait_seconds == 10
    assert g.last_verified == "never"
    assert g.invoicing_window_days is None
    assert g.ticket_field_map == (
        ("facturacion_folio", "invoice_data.facturacion_folio"),
        ("purchase_date", "purchase.date"),
        ("total", "purchase.total"),
    )


def test_www_domain_rejected(tmp_path):
    text = (FIXTURES / "guides" / "los-pollos-hermanos.md").read_text(encoding="utf-8")
    (tmp_path / "www.md").write_text(
        text.replace("domains: [lospolloshermanos.com.mx]", "domains: [www.lospolloshermanos.com.mx]"),
        encoding="utf-8",
    )
    with pytest.raises(GuideError) as exc:
        parse_guide(tmp_path / "www.md")
    assert str(exc.value) == (
        "www.md: match.domains entries must be bare eTLD+1, got 'www.lospolloshermanos.com.mx'"
    )


def test_missing_closing_fence_rejected(tmp_path):
    (tmp_path / "open.md").write_text("---\nid: open\nno closing fence", encoding="utf-8")
    with pytest.raises(GuideError) as exc:
        parse_guide(tmp_path / "open.md")
    assert str(exc.value) == "open.md: missing frontmatter closing '---'"
