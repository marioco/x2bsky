"""Tests for the neutral x2bsky development distribution."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"


def load_module(path: Path, name: str):
    """Load a module without running its command-line entry point."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def configure_neutral_environment(monkeypatch, runtime_dir: Path) -> None:
    monkeypatch.setenv("X2BSKY_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("X2BSKY_X_USERNAME", "test_account")
    monkeypatch.setenv("X2BSKY_MY_DOMAINS", "example.org")
    monkeypatch.setenv("X2BSKY_FALLBACK_URL", "https://example.org")


def installer_environment() -> dict[str, str]:
    """Return neutral values for a non-interactive installer test."""
    environment = os.environ.copy()
    environment.update(
        {
            "X2BSKY_X_USERNAME": "test_account",
            "X2BSKY_BLUESKY_HANDLE": "test.example",
            "X2BSKY_BLUESKY_APP_PASSWORD": "test-secret",
            "X2BSKY_MY_DOMAINS": "example.org,www.example.org",
            "X2BSKY_FALLBACK_URL": "https://example.org",
            "X2BSKY_CONFIRM": "yes",
        }
    )
    return environment


def test_configuration_requires_installer_values(monkeypatch, tmp_path: Path) -> None:
    for name in (
        "X2BSKY_X_USERNAME",
        "X2BSKY_MY_DOMAINS",
        "X2BSKY_FALLBACK_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("X2BSKY_RUNTIME_DIR", str(tmp_path))

    module = load_module(APP_DIR / "x2bsky_common.py", "missing_config_test")

    with pytest.raises(RuntimeError, match="X2BSKY_X_USERNAME"):
        module.validate_configuration()


def test_configuration_rejects_insecure_fallback_url(
    monkeypatch, tmp_path: Path
) -> None:
    configure_neutral_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("X2BSKY_FALLBACK_URL", "http://example.org")
    module = load_module(APP_DIR / "x2bsky_common.py", "insecure_url_test")

    with pytest.raises(RuntimeError, match="HTTPS-URL"):
        module.validate_configuration()


def test_import_does_not_create_runtime_directories(
    monkeypatch, tmp_path: Path
) -> None:
    runtime_dir = tmp_path / "must-not-exist"
    configure_neutral_environment(monkeypatch, runtime_dir)
    monkeypatch.syspath_prepend(str(APP_DIR))
    atproto = ModuleType("atproto")
    atproto.Client = object
    monkeypatch.setitem(sys.modules, "atproto", atproto)

    load_module(APP_DIR / "x2bsky.py", "import_side_effect_test")

    assert not runtime_dir.exists()


def test_run_lock_rejects_concurrent_execution(monkeypatch, tmp_path: Path) -> None:
    configure_neutral_environment(monkeypatch, tmp_path)
    monkeypatch.syspath_prepend(str(APP_DIR))
    atproto = ModuleType("atproto")
    atproto.Client = object
    monkeypatch.setitem(sys.modules, "atproto", atproto)
    module = load_module(APP_DIR / "x2bsky.py", "run_lock_test")
    module.prepare_runtime()
    first_lock = module.acquire_run_lock()
    try:
        with pytest.raises(RuntimeError, match="läuft bereits"):
            module.acquire_run_lock()
    finally:
        first_lock.close()


def test_media_url_string_is_not_processed_character_by_character(
    monkeypatch, tmp_path: Path
) -> None:
    configure_neutral_environment(monkeypatch, tmp_path)
    module = load_module(APP_DIR / "x2bsky_common.py", "media_url_type_test")
    post = module.Post(id="1", text="photo")

    module.apply_fxtwitter(
        post,
        {"mediaURLs": "https://pbs.twimg.com/media/Example"},
    )

    assert post.photos == ["https://pbs.twimg.com/media/Example?format=jpg&name=orig"]


def test_dry_run_does_not_persist_filtered_post(monkeypatch, tmp_path: Path) -> None:
    configure_neutral_environment(monkeypatch, tmp_path)
    monkeypatch.syspath_prepend(str(APP_DIR))
    atproto = ModuleType("atproto")
    atproto.Client = object
    monkeypatch.setitem(sys.modules, "atproto", atproto)
    module = load_module(APP_DIR / "x2bsky.py", "dry_run_test")
    post = SimpleNamespace(id="123", author="test_account", text="filtered", photos=[])
    state = {"last_id": "", "seen_images": []}
    mark_seen = Mock()
    save_state = Mock()

    monkeypatch.setattr(
        module, "load_bluesky_creds", lambda: ("test.example", "test-secret")
    )
    monkeypatch.setattr(module, "load_state", lambda **_kwargs: state)
    monkeypatch.setattr(module, "collect_posts", lambda **_kwargs: [post])
    monkeypatch.setattr(module, "select_new_posts", lambda _posts, _state: [post])
    monkeypatch.setattr(module, "skip_reason", lambda _post: "quote")
    monkeypatch.setattr(module, "mark_seen", mark_seen)
    monkeypatch.setattr(module, "save_state", save_state)

    assert module.main(["--dry-run"]) == 0
    mark_seen.assert_not_called()
    save_state.assert_not_called()


def test_login_check_authenticates_without_reading_or_posting(
    monkeypatch, tmp_path: Path
) -> None:
    configure_neutral_environment(monkeypatch, tmp_path)
    monkeypatch.syspath_prepend(str(APP_DIR))
    client = Mock()
    client_class = Mock(return_value=client)
    atproto = ModuleType("atproto")
    atproto.Client = client_class
    monkeypatch.setitem(sys.modules, "atproto", atproto)
    module = load_module(APP_DIR / "x2bsky.py", "login_check_test")
    collect_posts = Mock()
    monkeypatch.setattr(
        module, "load_bluesky_creds", lambda: ("test.example", "test-secret")
    )
    monkeypatch.setattr(module, "collect_posts", collect_posts)

    assert module.main(["--check-login"]) == 0

    client.login.assert_called_once_with("test.example", "test-secret")
    client.send_post.assert_not_called()
    client.send_images.assert_not_called()
    collect_posts.assert_not_called()


def test_installer_creates_complete_isolated_installation(tmp_path: Path) -> None:
    test_root = tmp_path / "installation"
    environment = installer_environment()

    subprocess.run(
        [str(ROOT / "install.sh"), "--test-root", str(test_root)],
        check=True,
        env=environment,
        timeout=30,
    )

    install_dir = test_root / "opt/x2bsky"
    runtime_dir = test_root / "var/lib/x2bsky"
    config_file = test_root / "etc/x2bsky/x2bsky.env"
    credential_file = runtime_dir / "credentials/bluesky.credentials"
    service_file = test_root / "etc/systemd/system/x2bsky.service"

    assert (install_dir / "app/x2bsky.py").is_file()
    assert (install_dir / "app/x2bsky_common.py").is_file()
    assert (install_dir / "docs/Installationsanleitung.docx").is_file()
    assert stat.S_IMODE(credential_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o640
    assert stat.S_IMODE(service_file.stat().st_mode) == 0o644
    assert credential_file.read_text(encoding="utf-8") == "test.example\ntest-secret\n"
    config_text = config_file.read_text(encoding="utf-8")
    assert "test-secret" not in config_text
    assert "test_account" in config_text
    service_text = service_file.read_text(encoding="utf-8")
    assert "@@" not in service_text
    assert str(install_dir) in service_text
    assert str(runtime_dir) in service_text
    assert (runtime_dir / "state/run.lock").is_file()

    state_file = runtime_dir / "state/state.json"
    state_file.write_text('{"last_id": "preserve-me"}\n', encoding="utf-8")
    subprocess.run(
        [str(ROOT / "install.sh"), "--test-root", str(test_root)],
        check=True,
        env=environment,
        timeout=30,
    )
    assert state_file.read_text(encoding="utf-8") == '{"last_id": "preserve-me"}\n'


def test_installer_refuses_foreign_installation_target(tmp_path: Path) -> None:
    test_root = tmp_path / "installation"
    foreign_dir = test_root / "opt/x2bsky"
    foreign_dir.mkdir(parents=True)
    foreign_file = foreign_dir / "foreign.txt"
    foreign_file.write_text("must remain untouched\n", encoding="utf-8")

    result = subprocess.run(
        [str(ROOT / "install.sh"), "--test-root", str(test_root)],
        check=False,
        capture_output=True,
        env=installer_environment(),
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "wird nicht von x2bsky verwaltet" in result.stderr
    assert foreign_file.read_text(encoding="utf-8") == "must remain untouched\n"


def test_installer_reports_missing_interactive_input(tmp_path: Path) -> None:
    environment = os.environ.copy()
    for name in (
        "X2BSKY_X_USERNAME",
        "X2BSKY_BLUESKY_HANDLE",
        "X2BSKY_BLUESKY_APP_PASSWORD",
        "X2BSKY_MY_DOMAINS",
        "X2BSKY_FALLBACK_URL",
    ):
        environment.pop(name, None)

    result = subprocess.run(
        [str(ROOT / "install.sh"), "--test-root", str(tmp_path / "installation")],
        check=False,
        capture_output=True,
        env=environment,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "Eingabe abgebrochen" in result.stderr


def test_installer_requires_confirmation_before_writing(tmp_path: Path) -> None:
    environment = installer_environment()
    environment.pop("X2BSKY_CONFIRM")
    test_root = tmp_path / "installation"

    result = subprocess.run(
        [str(ROOT / "install.sh"), "--test-root", str(test_root)],
        check=False,
        capture_output=True,
        env=environment,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "Keine Bestätigung möglich" in result.stderr
    assert not test_root.exists()


def test_installer_rejects_insecure_fallback_url(tmp_path: Path) -> None:
    environment = installer_environment()
    environment["X2BSKY_FALLBACK_URL"] = "http://example.org"

    result = subprocess.run(
        [str(ROOT / "install.sh"), "--test-root", str(tmp_path / "installation")],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "Fallback-URL" in result.stderr
    assert not (tmp_path / "installation").exists()


def test_distribution_contains_no_personal_or_legacy_identifiers() -> None:
    forbidden = (
        "gf" + "rei",
        "laura" + "xso",
        "mario" + "co",
        "xto" + "socialmedia",
        "_bluesky_" + "gfrei",
        "nit" + "ter",
        "tele" + "gram",
        "face" + "book",
        "xso" + "_",
    )
    files = [
        ROOT / "README.md",
        ROOT / "INSTALLATION.md",
        ROOT / "install.sh",
        ROOT / "systemd/x2bsky.service.in",
        ROOT / "systemd/x2bsky.timer",
        ROOT / "docs/ARCHITECTURE.md",
        ROOT / "app/x2bsky.py",
        ROOT / "app/x2bsky_common.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files).lower()
    combined = combined.replace("https://github.com/marioco/x2bsky.git", "")
    assert not [value for value in forbidden if value in combined]


def test_readme_contains_copyable_repository_installation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "sudo apt-get install -y git" in readme
    assert "git clone https://github.com/marioco/x2bsky.git" in readme
    assert "<REPOSITORY_URL>" not in readme
