from cfdi.guards import assert_guards, build_tools, fold, matched_stop_label

STOP = ("Emitir Factura", "Generar Factura y Enviar")


def test_fold_handles_case_and_accents():
    assert fold("EMITIR FACTURA") == "emitir factura"
    assert fold("Emitír  Factura ") == "emitir  factura"
    assert fold("Generar Factura y Enviar") == "generar factura y enviar"


def test_matched_stop_label_table():
    assert matched_stop_label(["Emitir Factura"], STOP) == "Emitir Factura"
    assert matched_stop_label(["EMITIR FACTURA"], STOP) == "Emitir Factura"
    assert matched_stop_label(["  emitír factura  "], STOP) == "Emitir Factura"
    assert matched_stop_label(["Por favor Emitir Factura ahora"], STOP) == "Emitir Factura"
    assert matched_stop_label(["Generar Factura y Enviar"], STOP) == "Generar Factura y Enviar"
    assert matched_stop_label(["Obtener Factura"], STOP) is None
    assert matched_stop_label(["Generar Factura"], STOP) is None
    assert matched_stop_label([], STOP) is None


def test_registry_safety_surface():
    tools = build_tools(STOP)
    actions = tools.registry.registry.actions

    assert "evaluate" not in actions
    assert "send_keys" not in actions
    assert "click" in actions
    assert actions["click"].description == (
        "Click element by index. Final-submit buttons are blocked and must be "
        "reported via ready_for_review instead."
    )
    assert "set_masked_input" in actions
    assert "ready_for_review" in actions

    assert_guards(tools)


def test_custom_action_params_are_strict_schema_compatible():
    """OpenAI strict structured output 400s on dict-typed (arbitrary-key map)
    action params — every property must be an enumerated type. Regression test
    for the run that failed 6/6 steps with 'Invalid schema for response_format'."""
    tools = build_tools(STOP)
    actions = tools.registry.registry.actions
    for name in ("ready_for_review", "set_masked_input"):
        schema = actions[name].param_model.model_json_schema()
        for prop_name, prop in schema.get("properties", {}).items():
            is_map = prop.get("type") == "object" and "additionalProperties" in prop
            assert not is_map, f"{name}.{prop_name} is a dict-typed param (breaks strict mode)"


def test_assert_guards_catches_unguarded_tools():
    from browser_use import Tools

    plain = Tools()
    try:
        assert_guards(plain)
    except AssertionError as exc:
        assert str(exc) == "guarded click is not installed — built-in click is active"
    else:
        raise AssertionError("assert_guards accepted unguarded Tools")
