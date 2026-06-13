import json
from datetime import date
from pathlib import Path

from cfdi.guides import load_guides, parse_guide
from cfdi.runner import POLICY, build_task, ground_truth

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).parent.parent

SAN_PABLO = parse_guide(REPO / "guides" / "farmacia-san-pablo.md")
SAMPLE_TICKET = json.loads((REPO / "examples" / "ticket.sample.json").read_text(encoding="utf-8"))
SAMPLE_FISCAL = json.loads((REPO / "fiscal.json.example").read_text(encoding="utf-8"))


def test_golden_prompt():
    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL, today=date(2026, 6, 12))
    assert prompt == (FIXTURES / "golden_prompt.txt").read_text(encoding="utf-8")


def test_prompt_contains_only_the_matched_guide():
    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL, today=date(2026, 6, 12))
    for other in load_guides(FIXTURES / "guides"):
        assert other.id not in prompt
        assert other.body not in prompt


def test_prompt_order_guide_first_values_last():
    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL, today=date(2026, 6, 12))
    assert prompt.index("# MERCHANT GUIDE") < prompt.index("# PATIENCE LIMITS") < prompt.index("# VALUES")
    assert prompt.rstrip().endswith("- {email} = you@example.com")


def test_ground_truth_names_stop_labels():
    assert ground_truth(SAN_PABLO) == (
        "Success means: the invoice form is completely filled with the provided "
        "values and verified, the final submit button (Emitir Factura / Generar "
        "Factura y Enviar) is visible but was NEVER clicked, and the agent called "
        "ready_for_review. Submitting the invoice means FAILURE."
    )


def test_transposed_ticket_date_is_resolved_in_prompt():
    amorino = parse_guide(REPO / "guides" / "amorino-gelato.md")
    ticket = {
        "invoice": {"invoice_number": "369636", "date": "2026-11-06"},
        "summary": {"total": 195.0},
    }
    prompt = build_task(amorino, ticket, SAMPLE_FISCAL, today=date(2026, 6, 12))
    assert "- {purchase_date} = 2026-06-11" in prompt
    assert "- {facturacion_folio} = 369636" in prompt
    assert "- {total} = 195.0" in prompt
    assert "2026-11-06" not in prompt


def test_write_report_uses_guide_field_map(tmp_path):
    from cfdi.runner import write_report

    amorino = parse_guide(REPO / "guides" / "amorino-gelato.md")
    ticket = {"invoice": {"invoice_number": "369636"}, "summary": {"total": 195.0}}
    path = write_report({"status": "aborted", "guide_id": "amorino-gelato"}, ticket, amorino, tmp_path)
    assert path.name.endswith("-amorino-gelato-369636.json")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "status": "aborted",
        "guide_id": "amorino-gelato",
        "ticket": {"folio": "369636", "total": 195.0},
    }


def test_policy_is_stable_and_mentions_the_three_mechanisms():
    assert "ready_for_review" in POLICY
    assert "set_masked_input" in POLICY
    assert "expected:" in POLICY
