import json

from cfdi.runner import (
    USERSCRIPT_EXTENSION,
    _build_and_inject_userscript,
    _extension_args,
)


def test_injector_extension_is_unpacked_mv3_main_world_js():
    manifest = json.loads((USERSCRIPT_EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert manifest["name"] == "CFDI Userscript Injector"
    assert manifest["content_scripts"] == [
        {
            "matches": ["<all_urls>"],
            "js": ["browseruse.js"],
            "run_at": "document_end",
            "world": "MAIN",
            "all_frames": False,
        }
    ]


def test_extension_args_load_only_our_extension():
    ext = str(USERSCRIPT_EXTENSION)
    assert _extension_args() == [
        "--enable-extensions",
        f"--disable-extensions-except={ext}",
        f"--load-extension={ext}",
    ]


def test_build_and_inject_writes_comment_free_content_script():
    _build_and_inject_userscript()
    injected = USERSCRIPT_EXTENSION / "browseruse.js"
    code = injected.read_text(encoding="utf-8")
    # The userscript metadata header is stripped; only the minified body remains.
    assert "==UserScript==" not in code
    assert not code.lstrip().startswith("//")
    assert "txt_cucfdi" in code  # the actual fix is present
