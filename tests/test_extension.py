import json
from pathlib import Path

from cfdi.runner import PAGE_MARKER_EXTENSION, _extension_args


def test_marker_extension_is_unpacked_mv3_css():
    manifest = json.loads((PAGE_MARKER_EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["name"] == "CFDI Run Marker"
    assert manifest["content_scripts"] == [
        {
            "matches": ["<all_urls>"],
            "css": ["border.css"],
            "run_at": "document_start",
            "all_frames": False,
        }
    ]


def test_border_css_is_10px_solid_black():
    css = (PAGE_MARKER_EXTENSION / "border.css").read_text(encoding="utf-8")
    assert css == "body {\n  border: 10px solid black !important;\n}\n"


def test_extension_args_load_only_our_extension():
    ext = str(PAGE_MARKER_EXTENSION)
    assert _extension_args() == [
        "--enable-extensions",
        f"--disable-extensions-except={ext}",
        f"--load-extension={ext}",
    ]
