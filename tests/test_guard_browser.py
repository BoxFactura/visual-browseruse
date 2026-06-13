"""Browser-level proof that the guarded click refuses the final-submit button.

Launches a real headless Chrome on a local HTML fixture. Gated behind an env
var because it needs a browser and ~15s:

    GUARD_BROWSER_TEST=1 uv run pytest tests/test_guard_browser.py -q
"""

import asyncio
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cfdi.guards import build_tools

requires_browser = pytest.mark.skipif(
    os.getenv("GUARD_BROWSER_TEST") != "1",
    reason="set GUARD_BROWSER_TEST=1 to run (launches headless Chrome)",
)

FIXTURES = Path(__file__).parent / "fixtures"


async def _exercise_guard(port: int):
    from browser_use import Browser

    tools = build_tools(("Emitir Factura",))
    browser = Browser(headless=True)
    await browser.start()
    try:
        await tools.registry.execute_action(
            "navigate",
            {"url": f"http://127.0.0.1:{port}/portal.html", "new_tab": False},
            browser_session=browser,
        )
        await asyncio.sleep(2)
        state = await browser.get_browser_state_summary()
        by_index = {
            index: node.get_all_children_text(max_depth=3).strip()
            for index, node in state.dom_state.selector_map.items()
        }

        def find(text: str) -> int:
            hits = [i for i, t in by_index.items() if text in t]
            assert hits, f"no element containing {text!r}; interactive elements: {by_index}"
            return hits[0]

        refused = await tools.registry.execute_action(
            "click", {"index": find("EMITIR FACTURA")}, browser_session=browser
        )
        allowed = await tools.registry.execute_action(
            "click", {"index": find("Obtener Factura")}, browser_session=browser
        )
        return refused, allowed
    finally:
        await browser.kill()


@requires_browser
def test_guarded_click_refuses_final_submit_on_real_page():
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        refused, allowed = asyncio.run(_exercise_guard(server.server_address[1]))
    finally:
        server.shutdown()

    assert refused.error.startswith("REFUSED: element ")
    assert refused.error.endswith(
        "matches the final-submit label 'Emitir Factura'. Never click it — when "
        "the form is complete and verified, call ready_for_review."
    )
    assert refused.is_done is False

    assert allowed.error is None
    assert "Clicked" in (allowed.extracted_content or "")
