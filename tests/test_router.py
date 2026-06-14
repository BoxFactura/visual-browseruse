from pathlib import Path

from cfdi.guides import load_guides
from cfdi.router import _pick_id

GUIDES = load_guides(Path(__file__).parent.parent / "guides")


def test_pick_id_validates_against_real_ids():
    # a clean id passes through
    assert _pick_id("farmacia-san-pablo", GUIDES) == "farmacia-san-pablo"
    # case / quotes / stray punctuation the model might add are tolerated
    assert _pick_id('"Amorino-Gelato".', GUIDES) == "amorino-gelato"
    assert _pick_id("  tiendas-extra  ", GUIDES) == "tiendas-extra"


def test_pick_id_rejects_unknown_or_none():
    assert _pick_id("none", GUIDES) is None
    assert _pick_id("some-portal-we-dont-have", GUIDES) is None
    assert _pick_id("", GUIDES) is None
