import json
from pathlib import Path

from cfdi.guides import load_guides
from cfdi.matcher import MatchResult, Signals, extract_signals, match, normalize_domain

FIXTURES = Path(__file__).parent / "fixtures"
GUIDES = load_guides(FIXTURES / "guides")


def ticket(name: str) -> dict:
    return json.loads((FIXTURES / "tickets" / f"{name}.json").read_text(encoding="utf-8"))


def test_normalize_domain_table():
    assert normalize_domain("www.farmaciasanpablo.com.mx") == "farmaciasanpablo.com.mx"
    assert normalize_domain("https://www.lospolloshermanos.com.mx/factura") == "lospolloshermanos.com.mx"
    assert normalize_domain("FACTURA.LosPollosHermanos.COM.MX") == "lospolloshermanos.com.mx"
    assert normalize_domain("") is None
    assert normalize_domain("not a url") is None


def test_extract_signals_scheme_less_url_and_rfc():
    assert extract_signals(ticket("los_pollos_hermanos")) == Signals(
        domain="lospolloshermanos.com.mx", rfc="PHE850315GH7"
    )


def test_extract_signals_zero_signals():
    assert extract_signals(ticket("lavanderia_brillante")) == Signals(domain=None, rfc=None)


def test_match_domain_and_rfc_agree():
    result = match(extract_signals(ticket("los_pollos_hermanos")), GUIDES)
    assert result == MatchResult(status="matched", guide_id="los-pollos-hermanos", tier="domain")


def test_match_rfc_only():
    result = match(Signals(domain=None, rfc="MEL721104RT2"), GUIDES)
    assert result == MatchResult(status="matched", guide_id="madrigal-electromotive", tier="rfc")


def test_match_cross_tier_conflict_names_both():
    result = match(extract_signals(ticket("madrigal_electromotive")), GUIDES)
    assert result == MatchResult(
        status="conflict", candidates=("los-pollos-hermanos", "madrigal-electromotive")
    )


def test_match_zero_signals_is_no_match():
    result = match(extract_signals(ticket("lavanderia_brillante")), GUIDES)
    assert result == MatchResult(status="no_match")


def test_match_unknown_signals_is_no_match():
    result = match(Signals(domain="heisenberg.com.mx", rfc="WWH580907KX1"), GUIDES)
    assert result == MatchResult(status="no_match")
