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


def test_sat_codes_expanded_to_names_in_values():
    from cfdi.runner import display_value

    assert display_value("regimen_fiscal", "603") == "603 - Personas Morales con Fines no Lucrativos"
    assert display_value("regimen_fiscal", "612") == "612 - Personas Físicas con Actividades Empresariales y Profesionales"
    assert display_value("uso_cfdi", "G03") == "G03 - Gastos en general"
    assert display_value("rfc", "UAP370423PP3") == "UAP370423PP3"
    assert display_value("regimen_fiscal", "999") == "999"

    fiscal = SAMPLE_FISCAL | {"regimen_fiscal": "603"}
    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, fiscal, today=date(2026, 6, 12))
    assert "- {regimen_fiscal} = 603 - Personas Morales con Fines no Lucrativos" in prompt
    assert "- {uso_cfdi} = G03 - Gastos en general" in prompt


def test_policy_explains_dropdown_name_matching():
    assert "603 - Personas Morales con Fines no Lucrativos" in POLICY
    assert "dropdowns of full SAT names" in POLICY


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
    report = {"status": "aborted", "guide_id": "amorino-gelato", "_held_open": False}
    path = write_report(report, ticket, amorino, tmp_path)
    assert path.name.endswith("-amorino-gelato-369636.json")
    # internal flags (underscore-prefixed) are stripped from the persisted report
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "status": "aborted",
        "guide_id": "amorino-gelato",
        "ticket": {"folio": "369636", "total": 195.0},
    }


def test_policy_is_stable_and_mentions_the_three_mechanisms():
    assert "ready_for_review" in POLICY
    assert "type_slowly" in POLICY
    assert "expected:" in POLICY


def test_auto_submit_task_and_policy():
    from cfdi.runner import POLICY_AUTO

    prompt = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL, today=date(2026, 6, 12),
                        auto_submit=True)
    assert "# MODE OVERRIDE — AUTO-SUBMIT" in prompt
    assert prompt.index("# MODE OVERRIDE — AUTO-SUBMIT") < prompt.index("# VALUES")
    # the opening line must NOT tell the agent not to submit (the bug that made it stop)
    assert prompt.splitlines()[0].startswith("Generate AND SUBMIT")
    assert "but do NOT submit" not in prompt
    # the override names the actual stop label so the agent knows what to click
    assert '"Emitir Factura"' in prompt
    # the guide body's supervised stop prose is stripped in auto mode (no contradiction);
    # the only remaining "NEVER click" is the override quoting it to suspend it
    assert "## Stop & completion" not in prompt
    assert "NEVER click it" not in prompt

    supervised = build_task(SAN_PABLO, SAMPLE_TICKET, SAMPLE_FISCAL, today=date(2026, 6, 12))
    assert "AUTO-SUBMIT" not in supervised
    assert "NEVER click it" in supervised  # supervised run keeps the guide's stop prose
    assert "## Stop & completion" in supervised

    assert "confirm_emission" in POLICY_AUTO
    assert "NEVER click a final submit button" not in POLICY_AUTO
    assert "type_slowly" in POLICY_AUTO


def test_auto_submit_strips_only_the_stop_section():
    from cfdi.runner import _strip_supervised_stop

    body = "## Steps\n1. do a thing\n\n## Error codes\n| x | y | z |\n\n## Stop & completion\nNEVER click it."
    assert _strip_supervised_stop(body) == "## Steps\n1. do a thing\n\n## Error codes\n| x | y | z |"


def test_ground_truth_auto_submit():
    assert ground_truth(SAN_PABLO, auto_submit=True) == (
        "Success means: the invoice form was filled with the provided values, "
        "the final submit button (Emitir Factura / Generar Factura y Enviar) WAS "
        "clicked, the portal visibly confirmed the invoice was emitted "
        "(confirmation message, folio fiscal or download links), and the agent "
        "called confirm_emission. Stopping before submission, or claiming "
        "emission without visible portal confirmation, means FAILURE."
    )


def test_derive_status_table():
    from cfdi.runner import derive_status

    ready = {"human_next_button": "Emitir Factura"}
    emitted = {"confirmation": "Factura generada. Folio fiscal: AAA-BBB"}

    assert derive_status(is_done=True, is_successful=True, is_validated=True, payload=ready) == "ready_for_review"
    assert derive_status(is_done=True, is_successful=True, is_validated=False, payload=ready) == "ready_for_review"
    assert derive_status(is_done=True, is_successful=True, is_validated=True, payload=emitted) == "submitted"
    assert derive_status(is_done=True, is_successful=True, is_validated=None, payload=emitted) == "submitted"
    assert derive_status(is_done=True, is_successful=True, is_validated=False, payload=emitted) == "judge_failed"
    assert derive_status(is_done=True, is_successful=True, is_validated=False, payload={}) == "judge_failed"
    assert derive_status(is_done=True, is_successful=False, is_validated=False, payload={}) == "aborted"
    assert derive_status(is_done=False, is_successful=False, is_validated=None, payload={}) == "incomplete_max_steps"
