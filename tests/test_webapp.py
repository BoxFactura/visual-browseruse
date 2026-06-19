import io

import pytest

from webapp import app, parse_json


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
    assert html.count("checked") == 2  # auto-submit + no-guide, both on by default


def test_run_requires_an_image(client):
    r = client.post("/run", data={"rfc": "UAP370423PP3"})
    assert r.status_code == 400
    assert r.get_json() == {"error": "no ticket image uploaded"}


def test_run_rejects_unknown_rfc_before_calling_openai(client):
    data = {"rfc": "ZZZ000000000", "image": (io.BytesIO(b"\xff\xd8\xff\xe0"), "t.jpg")}
    r = client.post("/run", data=data, content_type="multipart/form-data")
    assert r.status_code == 400
    assert r.get_json() == {"error": "unknown RFC 'ZZZ000000000'; add rfcs/ZZZ000000000.json"}
