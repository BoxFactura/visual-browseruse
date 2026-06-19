import io

import pytest

from webapp import app, parse_json, TRANSCRIBE_PROMPT


def test_transcribe_prompt_requests_facturadata_and_full_ticket():
    # the matcher depends on the facturadata block; the agent depends on the rest
    assert "facturadata" in TRANSCRIBE_PROMPT
    assert "store_name" in TRANSCRIBE_PROMPT
    assert "EVERY" in TRANSCRIBE_PROMPT  # whole-ticket transcription, not just facturadata


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def test_parse_json_plain_object():
    assert parse_json('{"facturadata": {"store_name": "Bodegas Alianza"}}') == {
        "facturadata": {"store_name": "Bodegas Alianza"}
    }


def test_parse_json_strips_fence_and_prose():
    fenced = 'Sure!\n```json\n{"facturadata": {"store_name": "X"}}\n```'
    assert parse_json(fenced) == {"facturadata": {"store_name": "X"}}


def test_parse_json_rejects_non_object():
    assert parse_json("not json at all") is None
    assert parse_json("[1, 2, 3]") is None  # a JSON array is not a ticket object


def test_index_lists_rfcs_and_ships_both_flags_checked(client):
    html = client.get("/").get_data(as_text=True)
    assert "<option" in html
    # both flags ship checked by default
    assert '<input type="checkbox" name="auto_submit" checked>' in html
    assert '<input type="checkbox" name="no_guide" checked>' in html
    # fail-safe: the form posts to /run as multipart even if JS never runs
    assert '<form id="f" method="post" action="/run" enctype="multipart/form-data">' in html


def test_run_requires_an_image(client):
    r = client.post("/run", data={"rfc": "UAP370423PP3"})
    assert r.status_code == 400
    assert r.get_json() == {"error": "no ticket image uploaded"}


def test_run_rejects_unknown_rfc_before_calling_openai(client):
    data = {"rfc": "ZZZ000000000", "image": (io.BytesIO(b"\xff\xd8\xff\xe0"), "t.jpg")}
    r = client.post("/run", data=data, content_type="multipart/form-data")
    assert r.status_code == 400
    assert r.get_json() == {"error": "unknown RFC 'ZZZ000000000'; add rfcs/ZZZ000000000.json"}
