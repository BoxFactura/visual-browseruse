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
    assert guide.portal_url == "https://www.farmaciasanpablo.com.mx/electronic-billing"
    assert guide.stop_before_labels == ("Emitir Factura", "Generar Factura y Enviar")
    assert guide.invoicing_window_days == 180
    assert guide.patience_max_reload_cycles == 3
    assert "set_masked_input" in guide.body
    assert "CFDI40147" in guide.body
    assert "NEVER click it" in guide.body


def test_load_guides_returns_both_fixtures():
    guides = load_guides(FIXTURES / "guides")
    assert [g.id for g in guides] == ["los-pollos-hermanos", "madrigal-electromotive"]


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
        "bad.md: missing frontmatter keys: description, match, portal_url, "
        "required_ticket_fields, required_fiscal_fields, stop, patience, last_verified"
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
