import json
from pathlib import Path

from cfdi.guides import load_guides, parse_guide
from cfdi.runner import POLICY, build_task, ground_truth

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parent.parent

SAN_PABLO = parse_guide(REPO / "guides" / "farmacia-san-pablo.md")
SAMPLE_TICKET = json.loads((REPO / "examples" / "ticket.sample.json").read_text(encoding="utf-8"))
SAMPLE_FISCAL = json.loads((REPO / "fiscal.json.example").read_text(encoding="utf-8"))


def test_golden_prompt():
    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL)
    assert prompt == (FIXTURES / "golden_prompt.txt").read_text(encoding="utf-8")


def test_prompt_contains_only_the_matched_guide():
    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL)
    for other in load_guides(FIXTURES / "guides"):
        assert other.id not in prompt
        assert other.body not in prompt


def test_prompt_order_guide_first_values_last():
    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL)
    assert prompt.index("# MERCHANT GUIDE") < prompt.index("# PATIENCE LIMITS") < prompt.index("# VALUES")
    assert prompt.rstrip().endswith("- {email} = you@example.com")


def test_ground_truth_names_stop_labels():
    assert ground_truth(SAN_PABLO) == (
        "Success means: the invoice form is completely filled with the provided "
        "values and verified, the final submit button (Emitir Factura / Generar "
        "Factura y Enviar) is visible but was NEVER clicked, and the agent called "
        "ready_for_review. Submitting the invoice means FAILURE."
    )


def test_policy_is_stable_and_mentions_the_three_mechanisms():
    assert "ready_for_review" in POLICY
    assert "set_masked_input" in POLICY
    assert "expected:" in POLICY
