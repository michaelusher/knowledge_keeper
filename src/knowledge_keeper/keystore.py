"""Local storage for API keys entered through the GUI.

Keys are written to a per-user config file — never to the project folder,
config.yaml, or anything git would pick up:

    Linux    $XDG_CONFIG_HOME/knowledge-keeper/keys.env  (~/.config/...)
    macOS    ~/Library/Application Support/knowledge-keeper/keys.env
    Windows  %APPDATA%\\knowledge-keeper\\keys.env

The file is created with owner-only permissions (0600) on Linux/macOS.
Values already present in the real environment always win, so a key you
`export` in your shell overrides a saved one.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Only these names can be saved — they're the keys the providers read.
ALLOWED_KEYS = {
    "GEMINI_API_KEY": "Google Gemini",
    "ANTHROPIC_API_KEY": "Anthropic",
    "KK_AZURE__OPENAI_API_KEY": "Azure OpenAI",
    "KK_AZURE__SEARCH_API_KEY": "Azure AI Search",
}

_loaded_from_file: set[str] = set()
_session_only: set[str] = set()


def keystore_dir() -> Path:
    override = os.environ.get("KK_KEYSTORE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "knowledge-keeper"


def keystore_path() -> Path:
    return keystore_dir() / "keys.env"


def _read() -> dict[str, str]:
    path = keystore_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        out[name.strip()] = value.strip()
    return out


def _write(values: dict[str, str]) -> None:
    path = keystore_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# knowledge-keeper saved API keys — do not share or commit this file\n"
    body += "".join(f"{k}={v}\n" for k, v in sorted(values.items()))
    # Create with restrictive permissions before writing any secret into it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_into_env() -> None:
    """Copy saved keys into os.environ unless the environment already sets them."""
    for name, value in _read().items():
        if name in ALLOWED_KEYS and value and not os.environ.get(name):
            os.environ[name] = value
            _loaded_from_file.add(name)


def save(name: str, value: str, remember: bool = True) -> None:
    if name not in ALLOWED_KEYS:
        raise ValueError(f"Unsupported key name: {name}")
    value = value.strip()
    if not value:
        raise ValueError("Key is empty")
    os.environ[name] = value
    if remember:
        values = _read()
        values[name] = value
        _write(values)
        _loaded_from_file.add(name)
        _session_only.discard(name)
    else:
        _loaded_from_file.discard(name)
        _session_only.add(name)


def forget(name: str) -> None:
    values = _read()
    if name in values:
        values.pop(name)
        _write(values)
    if name in _loaded_from_file or name in _session_only:
        os.environ.pop(name, None)
        _loaded_from_file.discard(name)
        _session_only.discard(name)


def describe(name: str) -> dict:
    """Status for display — never returns the key itself."""
    value = os.environ.get(name, "")
    saved = name in _read()
    if not value:
        source = "missing"
    elif name in _loaded_from_file:
        source = "saved"
    elif name in _session_only:
        source = "session"
    else:
        source = "environment"
    return {
        "name": name,
        "label": ALLOWED_KEYS.get(name, name),
        "present": bool(value),
        "source": source,
        "saved_on_disk": saved,
        "hint": (value[:4] + "…" + value[-2:]) if len(value) >= 10 else ("set" if value else ""),
    }
