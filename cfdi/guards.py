"""Safety-critical browser actions. Keep this module small and auditable.

Three properties enforced here, mechanically rather than by prompt:
1. Supervised mode (default): the agent CANNOT click a final-submit button —
   the built-in `click` action is overwritten (registry registration is a plain
   dict assignment, no collision guard) by a guard that refuses elements
   matching the guide's stop labels, and the run ends via `ready_for_review`.
2. The agent has no raw-JS or keyboard escape hatch in either mode: `evaluate`
   and `send_keys` are excluded; `set_masked_input` is the one bounded helper.
3. Auto-submit mode (explicit --auto-submit): the deny-list is emptied and the
   run ends via `confirm_emission` after the portal confirms emission; the
   judge must agree or the run reports judge_failed, not submitted.

Pinned to browser-use==0.12.9 — the guard delegates to Tools._click_by_index
to keep all built-in click behavior (new-tab detection, <select> handling).
"""

import asyncio
import json
import unicodedata

from browser_use import Tools
from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession
from browser_use.tools.views import ClickElementActionIndexOnly

GUARDED_CLICK_MARKER = "Click element by index. Final-submit buttons are blocked"


def fold(text: str) -> str:
    """Lowercase and strip accents so 'EMITIR FACTURA' matches 'Emitír factura'."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


def element_texts(node) -> list[str]:
    """Texts an element exposes to a user: child text plus label-ish attributes."""
    texts = [node.get_all_children_text(max_depth=3)]
    attrs = node.attributes or {}
    texts.extend(attrs.get(a, "") for a in ("aria-label", "value", "title"))
    return [t for t in texts if t and t.strip()]


def matched_stop_label(texts: list[str], stop_labels: tuple[str, ...]) -> str | None:
    """The stop label an element matches, or None. Case- and accent-insensitive."""
    folded_texts = [fold(t) for t in texts]
    for label in stop_labels:
        needle = fold(label)
        if any(needle in t for t in folded_texts):
            return label
    return None


SET_MASKED_INPUT_JS = """
function(digits) {
    const proto = this instanceof HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    this.focus();
    setter.call(this, '');
    this.dispatchEvent(new Event('input', {bubbles: true}));
    setter.call(this, digits);
    this.dispatchEvent(new Event('input', {bubbles: true}));
    this.dispatchEvent(new Event('change', {bubbles: true}));
    this.blur();
    return this.value;
}
"""


def build_tools(stop_labels: tuple[str, ...], *, auto_submit: bool = False) -> Tools:
    """Supervised mode (default): final-submit clicks are refused and the run
    ends via ready_for_review. auto_submit=True: the built-in click stays (no
    deny-list) and the run ends via confirm_emission AFTER the agent submits."""
    tools = Tools()
    # No raw JS (could form.submit()) and no keyboard shortcuts (Enter submits a
    # focused form); no file-system scratchpad actions (irrelevant to invoicing —
    # the agent wandered into write_file/replace_file note-taking, adding steps).
    # exclude_action removes the action AND blocks re-registration.
    for action in ("evaluate", "send_keys", "write_file", "read_file", "replace_file"):
        tools.exclude_action(action)

    if not auto_submit:

        @tools.action(
            f"{GUARDED_CLICK_MARKER} and must be reported via ready_for_review instead.",
            param_model=ClickElementActionIndexOnly,
        )
        async def click(params: ClickElementActionIndexOnly, browser_session: BrowserSession):
            node = await browser_session.get_element_by_index(params.index)
            if node is not None:
                label = matched_stop_label(element_texts(node), stop_labels)
                if label is not None:
                    return ActionResult(
                        error=(
                            f"REFUSED: element {params.index} matches the final-submit label "
                            f"'{label}'. Never click it — when the form is complete and "
                            f"verified, call ready_for_review."
                        )
                    )
            return await tools._click_by_index(params, browser_session)

    @tools.action(
        "Set a currency/number-masked input reliably. Pass the element index and the "
        "DIGITS ONLY (no decimal point, no symbols: '47400' for $474.00). Typing into "
        "masked fields mangles values; this sets the value and fires the mask's events. "
        "Returns the field's resulting visible value — verify it before submitting."
    )
    async def set_masked_input(index: int, digits: str, browser_session: BrowserSession):
        if not digits.isdigit():
            return ActionResult(error=f"set_masked_input takes digits only, got {digits!r}")
        node = await browser_session.get_element_by_index(index)
        if node is None:
            return ActionResult(error=f"Element index {index} not available - refresh browser state.")

        cdp_session = await browser_session.get_or_create_cdp_session()
        resolved = await cdp_session.cdp_client.send.DOM.resolveNode(
            params={"backendNodeId": node.backend_node_id},
            session_id=cdp_session.session_id,
        )
        if "object" not in resolved or "objectId" not in resolved["object"]:
            return ActionResult(error=f"Could not resolve element {index} to a DOM object.")
        js_result = await cdp_session.cdp_client.send.Runtime.callFunctionOn(
            params={
                "objectId": resolved["object"]["objectId"],
                "functionDeclaration": SET_MASKED_INPUT_JS,
                "arguments": [{"value": digits}],
                "returnByValue": True,
            },
            session_id=cdp_session.session_id,
        )
        if js_result.get("exceptionDetails"):
            return ActionResult(error=f"set_masked_input failed: {js_result['exceptionDetails']}")
        value = js_result.get("result", {}).get("value", "")
        memory = f"set_masked_input({digits!r}) on element {index}; field now reads {value!r}"
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    @tools.action(
        "Type into a field one real keystroke at a time, with a delay between keys, so a "
        "badly-implemented input mask processes each keypress. Use this for currency/number "
        "masks that mangle a set value (e.g. type the digits '230600' and the mask builds "
        "'$2,306.00'). index = the field; text = exactly the characters to type; delay_ms "
        "between keystrokes (default 90)."
    )
    async def type_slowly(index: int, text: str, browser_session: BrowserSession, delay_ms: int = 90):
        node = await browser_session.get_element_by_index(index)
        if node is None:
            return ActionResult(error=f"Element index {index} not available - refresh browser state.")
        cdp = await browser_session.get_or_create_cdp_session()
        resolved = await cdp.cdp_client.send.DOM.resolveNode(
            params={"backendNodeId": node.backend_node_id}, session_id=cdp.session_id,
        )
        obj = resolved.get("object", {}).get("objectId")
        if not obj:
            return ActionResult(error=f"Could not resolve element {index} to a DOM object.")
        # focus and clear the field first
        await cdp.cdp_client.send.Runtime.callFunctionOn(
            params={"objectId": obj, "functionDeclaration":
                    "function(){this.focus();const p=this.tagName==='TEXTAREA'?"
                    "HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
                    "Object.getOwnPropertyDescriptor(p,'value').set.call(this,'');"
                    "this.dispatchEvent(new Event('input',{bubbles:true}));}"},
            session_id=cdp.session_id,
        )
        for ch in text:
            await cdp.cdp_client.send.Input.dispatchKeyEvent(
                params={"type": "keyDown", "key": ch}, session_id=cdp.session_id)
            await cdp.cdp_client.send.Input.dispatchKeyEvent(
                params={"type": "char", "text": ch, "key": ch}, session_id=cdp.session_id)
            await cdp.cdp_client.send.Input.dispatchKeyEvent(
                params={"type": "keyUp", "key": ch}, session_id=cdp.session_id)
            await asyncio.sleep(max(0, delay_ms) / 1000)
        result = await cdp.cdp_client.send.Runtime.callFunctionOn(
            params={"objectId": obj, "returnByValue": True, "functionDeclaration":
                    "function(){this.dispatchEvent(new Event('change',{bubbles:true}));"
                    "this.blur();return this.value;}"},
            session_id=cdp.session_id,
        )
        value = result.get("result", {}).get("value", "")
        memory = f"type_slowly({text!r}) into element {index}; field now reads {value!r}"
        return ActionResult(extracted_content=memory, long_term_memory=memory)

    # NOTE: params must be strict-JSON-schema friendly — OpenAI's structured
    # output rejects dict[...] (arbitrary-key map) parameters with a 400 on
    # every step. Strings and typed lists only.
    if auto_submit:

        @tools.action(
            "Call this ONLY after you clicked the final submit (and any confirmation "
            "dialog) and the portal CONFIRMS the invoice was emitted. Put the portal's "
            "verbatim confirmation (message, folio fiscal/UUID, download links) in "
            "'confirmation'. fields_filled takes one 'label = value' per line. "
            "This ends the run successfully."
        )
        async def confirm_emission(
            final_url: str,
            confirmation: str,
            fields_filled: str,
            portal_errors_verbatim: list[str],
        ):
            payload = {
                "final_url": final_url,
                "confirmation": confirmation,
                "fields_filled": fields_filled,
                "portal_errors_verbatim": portal_errors_verbatim,
            }
            return ActionResult(
                is_done=True,
                success=True,
                extracted_content=json.dumps(payload, ensure_ascii=False),
            )

        return tools

    @tools.action(
        "Call this ONLY when every form field is filled and verified and the single "
        "remaining action is the final submit button (which a human will click). "
        "fields_filled/fields_empty take one 'label = value' per line ('none' if empty). "
        "This ends the run successfully."
    )
    async def ready_for_review(
        final_url: str,
        human_next_button: str,
        fields_filled: str,
        fields_empty: str,
        portal_errors_verbatim: list[str],
    ):
        payload = {
            "final_url": final_url,
            "human_next_button": human_next_button,
            "fields_filled": fields_filled,
            "fields_empty": fields_empty,
            "portal_errors_verbatim": portal_errors_verbatim,
        }
        return ActionResult(
            is_done=True,
            success=True,
            extracted_content=json.dumps(payload, ensure_ascii=False),
        )

    return tools


def assert_guards(tools: Tools, *, auto_submit: bool = False) -> None:
    """Hard assertions that the safety surface is intact. Call after Agent setup."""
    actions = tools.registry.registry.actions
    click_action = actions.get("click")
    assert click_action is not None, "no click action registered"
    if not auto_submit:
        assert click_action.description.startswith(GUARDED_CLICK_MARKER), (
            "guarded click is not installed — built-in click is active"
        )
    assert "evaluate" not in actions, "raw-JS 'evaluate' action is still registered"
    assert "send_keys" not in actions, "'send_keys' action is still registered"
    assert tools._coordinate_clicking_enabled is False, (
        "coordinate clicking is enabled — it bypasses the index-based click guard"
    )
    done_action = "confirm_emission" if auto_submit else "ready_for_review"
    assert done_action in actions and "set_masked_input" in actions
