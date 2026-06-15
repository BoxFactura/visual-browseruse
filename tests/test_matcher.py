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
    # both the URL domain and issuer RFC point at the same guide; RFC (the more
    # precise signal) is the reported tier
    result = match(extract_signals(ticket("los_pollos_hermanos")), GUIDES)
    assert result == MatchResult(status="matched", guide_id="los-pollos-hermanos", tier="rfc")


def test_extract_signals_reads_facturadata():
    t = {"facturadata": {"store_name": "Madrigal  Electromotive", "rfc": "mel721104rt2"}}
    assert extract_signals(t) == Signals(
        domain=None, rfc="MEL721104RT2", store_name="madrigal electromotive"
    )


def test_facturadata_rfc_takes_precedence_over_issuer_rfc():
    t = {"facturadata": {"rfc": "MEL721104RT2"}, "issuer": {"rfc": "PHE850315GH7"}}
    assert extract_signals(t).rfc == "MEL721104RT2"


def test_match_by_store_name_only():
    result = match(Signals(domain=None, rfc=None, store_name="madrigal electromotive"), GUIDES)
    assert result == MatchResult(status="matched", guide_id="madrigal-electromotive", tier="name")


def test_match_facturadata_name_and_rfc_agree():
    t = {"facturadata": {"store_name": "Madrigal Electromotive", "rfc": "MEL721104RT2"}}
    result = match(extract_signals(t), GUIDES)
    assert result == MatchResult(status="matched", guide_id="madrigal-electromotive", tier="rfc")


def test_real_alianza_ticket_matches_bodegas_guide():
    # end to end on the real artifacts: tickets/alianza.json carries facturadata
    # (store_name + rfc) and resolves to the real guides/bodegas-alianza.md
    repo = Path(__file__).parent.parent
    real_guides = load_guides(repo / "guides")
    alianza = json.loads((repo / "tickets" / "alianza.json").read_text(encoding="utf-8"))
    assert extract_signals(alianza) == Signals(
        domain=None, rfc="CVA991118C63", store_name="bodegas alianza"
    )
    result = match(extract_signals(alianza), real_guides)
    assert result == MatchResult(status="matched", guide_id="bodegas-alianza", tier="rfc")


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
