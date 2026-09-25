"""Create an app-menu shortcut that launches Knowledge Keeper without a terminal.

The shortcut runs this exact Python interpreter (`python -m knowledge_keeper.gui`),
so it works even if the `kk-gui` command isn't on the desktop session's PATH.

    Linux    ~/.local/share/applications/knowledge-keeper.desktop  (+ icon)
    macOS    ~/Applications/Knowledge Keeper.app
    Windows  Start Menu\\Programs\\Knowledge Keeper.lnk
"""
from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ICON = Path(__file__).parent / "web" / "static" / "favicon.svg"


def _python_for_gui() -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = exe.with_name("pythonw.exe")  # no console window
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def _desktop_quote(arg: str) -> str:
    """Quote one Exec= argument per the freedesktop Desktop Entry spec."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg.replace("%", "%%")
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return '"' + escaped.replace("%", "%%") + '"'


def install_shortcut(workspace: Path) -> dict:
    workspace = Path(workspace)
    if sys.platform.startswith("linux"):
        return _install_linux(workspace)
    if sys.platform == "darwin":
        return _install_macos(workspace)
    if sys.platform == "win32":
        return _install_windows(workspace)
    raise RuntimeError(f"Shortcuts aren't supported on {sys.platform}")


def _install_linux(workspace: Path) -> dict:
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    apps = data / "applications"
    icons = data / "icons" / "hicolor" / "scalable" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, icons / "knowledge-keeper.svg")

    cmd = " ".join(
        _desktop_quote(p) for p in [_python_for_gui(), "-m", "knowledge_keeper.gui", "--workspace", str(workspace)]
    )
    entry = apps / "knowledge-keeper.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Knowledge Keeper\n"
        "Comment=Ask questions about your documents and find knowledge gaps\n"
        f"Exec={cmd}\n"
        f"Icon={icons / 'knowledge-keeper.svg'}\n"
        "Terminal=false\n"
        "Categories=Office;Education;\n"
        "StartupNotify=false\n", encoding="utf-8")
    entry.chmod(entry.stat().st_mode | stat.S_IXUSR)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps)], check=False, capture_output=True)
    return {
        "path": str(entry),
        "message": "Added “Knowledge Keeper” to your app menu. Search for it in Activities (it may take a few seconds to appear).",
    }


def _install_macos(workspace: Path) -> dict:
    app = Path.home() / "Applications" / "Knowledge Keeper.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    launcher = macos / "knowledge-keeper"
    # Start the server in the background and exit right away. If the launcher kept
    # running, macOS would treat the app as open and ignore later clicks (so closing
    # the browser tab would leave no way back in). Exiting means every click re-runs
    # it, and kk-gui simply reopens the browser when the server is already up.
    launcher.write_text(
        "#!/bin/sh\n"
        f"nohup {shlex.quote(_python_for_gui())} -m knowledge_keeper.gui --workspace {shlex.quote(str(workspace))}"
        " >/dev/null 2>&1 &\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    (app / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        "<key>CFBundleName</key><string>Knowledge Keeper</string>\n"
        "<key>CFBundleIdentifier</key><string>io.github.knowledge-keeper</string>\n"
        "<key>CFBundleExecutable</key><string>knowledge-keeper</string>\n"
        "<key>CFBundlePackageType</key><string>APPL</string>\n"
        "<key>LSUIElement</key><true/>\n"
        "</dict></plist>\n",
        encoding="utf-8",
    )
    return {"path": str(app), "message": f"Created {app}. Open it from Launchpad or ~/Applications."}


def _install_windows(workspace: Path) -> dict:
    programs = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    link = programs / "Knowledge Keeper.lnk"

    def ps_quote(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + ps_quote(str(link)) + ");"
        "$s.TargetPath = " + ps_quote(_python_for_gui()) + ";"
        "$s.Arguments = " + ps_quote(f'-m knowledge_keeper.gui --workspace "{workspace}"') + ";"
        "$s.WorkingDirectory = " + ps_quote(str(workspace)) + ";"
        "$s.Description = 'Knowledge Keeper';"
        "$s.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True)
    return {"path": str(link), "message": "Added Knowledge Keeper to your Start menu."}
