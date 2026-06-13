from cfdi.trace import StepTrace


def sample_trace() -> StepTrace:
    t = StepTrace()
    t.steps = [
        {"step": 1, "url": "u", "eval": "navegación exitosa", "goal": "fill",
         "actions": [{"name": "input", "index": 10}, {"name": "input", "index": 11}]},
        {"step": 2, "url": "u", "eval": "éxito", "goal": "advance",
         "actions": [{"name": "click", "index": 174}]},
        {"step": 3, "url": "u", "eval": "éxito parcial con bloqueo temporal del SPA", "goal": "wait",
         "actions": [{"name": "wait", "seconds": 10}, {"name": "scroll"}]},
    ]
    return t


def test_friction_summary_flags_wasted_and_trouble_steps():
    assert sample_trace().friction_summary() == {
        "total_steps": 3,
        "action_counts": {"input": 2, "click": 1, "wait": 1, "scroll": 1},
        "wasted_action_count": 2,
        "friction_steps": [3],
    }


def test_render_is_compact_and_step_indexed():
    assert sample_trace().render() == (
        "[1] input#10, input#11\n"
        "      eval: navegación exitosa\n"
        "      goal: fill\n"
        "[2] click#174\n"
        "      eval: éxito\n"
        "      goal: advance\n"
        "[3] wait, scroll\n"
        "      eval: éxito parcial con bloqueo temporal del SPA\n"
        "      goal: wait"
    )


def test_write_reads_back_as_jsonl(tmp_path):
    import json

    path = tmp_path / "t.jsonl"
    sample_trace().write(path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["step"] for r in rows] == [1, 2, 3]
    assert rows[2]["actions"] == [{"name": "wait", "seconds": 10}, {"name": "scroll"}]
