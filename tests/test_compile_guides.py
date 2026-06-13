from pathlib import Path

import pytest

from cfdi.guides import GuideError, parse_guide
from compile_guides import extract_image_urls, strip_code_fence, strip_leaked_rules


def test_strip_leaked_rules():
    leaked = "---\nid: x\n---\n## Stop & completion\nNEVER click it.\n\nRULES\n- rule one\n- rule two\n"
    assert strip_leaked_rules(leaked) == "---\nid: x\n---\n## Stop & completion\nNEVER click it.\n"
    clean = "---\nid: x\n---\n## Stop & completion\nNEVER click it.\n"
    assert strip_leaked_rules(clean) == "---\nid: x\n---\n## Stop & completion\nNEVER click it.\n"

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_image_urls():
    markdown = (
        "Paso 1\n\n![](https://i.imgur.com/6UoeAxj.png?1)\n\ntexto\n\n"
        "![alt text](https://i.imgur.com/QxhFM0J.png?1)\n\n[link](https://example.com)\n"
    )
    assert extract_image_urls(markdown) == [
        "https://i.imgur.com/6UoeAxj.png?1",
        "https://i.imgur.com/QxhFM0J.png?1",
    ]


def test_strip_code_fence_table():
    assert strip_code_fence("---\nid: x\n---\nbody") == "---\nid: x\n---\nbody\n"
    assert strip_code_fence("```markdown\n---\nid: x\n---\nbody\n```") == "---\nid: x\n---\nbody\n"
    assert strip_code_fence("```\n---\nid: x\n---\nbody\n```\n") == "---\nid: x\n---\nbody\n"


def review_required_guide(tmp_path) -> Path:
    text = (FIXTURES / "guides" / "los-pollos-hermanos.md").read_text(encoding="utf-8")
    path = tmp_path / "draft.md"
    path.write_text(
        text.replace('before_labels: ["Facturar"]', "before_labels: [REVIEW_REQUIRED]"),
        encoding="utf-8",
    )
    return path


def test_runner_loader_rejects_review_required(tmp_path):
    path = review_required_guide(tmp_path)
    with pytest.raises(GuideError) as exc:
        parse_guide(path)
    assert str(exc.value) == (
        "draft.md: stop.before_labels contains the compiler placeholder "
        "REVIEW_REQUIRED — a human must confirm the real final-submit label first"
    )


def test_compiler_validation_accepts_review_required_drafts(tmp_path):
    path = review_required_guide(tmp_path)
    guide = parse_guide(path, allow_review_placeholder=True)
    assert guide.stop_before_labels == ("REVIEW_REQUIRED",)
