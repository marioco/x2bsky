"""Distribution tests for the Bluesky-only crossposter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    """Load a module without executing its command-line entry point."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def test_common_defaults_to_repository_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XSO_HOME_DIR", str(tmp_path))
    monkeypatch.delenv("XSO_SCRIPTS_DIR", raising=False)

    module = load_module(ROOT / "xso_common.py", "x2bsky_common_path_test")

    assert module.SCRIPTS_DIR == ROOT
    assert module.STATE_DIR == tmp_path / "tmp/xtosocialmedia"


def test_dry_run_does_not_persist_filtered_post(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setenv("XSO_HOME_DIR", str(tmp_path))
    monkeypatch.delenv("XSO_SCRIPTS_DIR", raising=False)

    atproto = ModuleType("atproto")
    atproto.Client = object
    monkeypatch.setitem(sys.modules, "atproto", atproto)
    module = load_module(ROOT / "x2bsky.py", "x2bsky_dry_run_test")
    post = SimpleNamespace(id="123", author="account", text="filtered", photos=[])
    state = {"last_id": "", "seen_images": []}
    mark_seen = Mock()
    save_state = Mock()

    monkeypatch.setattr(module, "load_bluesky_creds", lambda: ("handle", "password"))
    monkeypatch.setattr(module, "load_state", lambda **_kwargs: state)
    monkeypatch.setattr(module, "collect_posts", lambda **_kwargs: [post])
    monkeypatch.setattr(module, "select_new_posts", lambda _posts, _state: [post])
    monkeypatch.setattr(module, "skip_reason", lambda _post: "quote")
    monkeypatch.setattr(module, "mark_seen", mark_seen)
    monkeypatch.setattr(module, "save_state", save_state)

    assert module.main(["--dry-run"]) == 0
    mark_seen.assert_not_called()
    save_state.assert_not_called()
