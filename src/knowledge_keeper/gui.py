"""Desktop launcher: `kk-gui` starts the local website and opens it in your browser.

    kk-gui                       open the app (reuses a running copy if there is one)
    kk-gui --share               also allow other devices on your network (access code required)
    kk-gui --workspace PATH      use a specific knowledge-base folder
    kk-gui --no-browser          just run the server (for frontend development)
    kk-gui --install-shortcut    add Knowledge Keeper to your app menu, then exit

Launched from an app-menu shortcut there's no terminal, so startup problems are
also written to <workspace>/kk_data/gui.log.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

DEFAULT_PORT = 8765


def _health(port: int) -> Optional[dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.8) as r:
            data = json.loads(r.read())
            return data if data.get("app") == "knowledge-keeper" else None
    except Exception:
        return None


def _port_free(port: int, host: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # On Windows SO_REUSEADDR lets you bind a port that's already in use,
        # which would defeat this check, so only set it elsewhere.
        if sys.platform != "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _lan_ip() -> str:
    """Best guess at this machine's address on the local network (sends no packets)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _open_when_ready(url: str, port: int) -> None:
    for _ in range(100):
        if _health(port):
            webbrowser.open(url)
            return
        time.sleep(0.1)


def _ensure_std_streams() -> None:
    """Windows runs `kk-gui` and the Start-menu shortcut with pythonw (no console),
    where sys.stdout/sys.stderr are None and any print() would crash the app
    before it starts. Point them at devnull; the log file still records everything."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    _ensure_std_streams()
    parser = argparse.ArgumentParser(prog="kk-gui", description="Open Knowledge Keeper in your web browser.")
    parser.add_argument("--workspace", help="knowledge-base folder (default: ~/KnowledgeKeeper)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--share", action="store_true", help="allow other devices on your network (with an access code)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    parser.add_argument("--install-shortcut", action="store_true", help="add Knowledge Keeper to your app menu and exit")
    args = parser.parse_args(argv)

    from .service import resolve_workspace

    workspace = resolve_workspace(args.workspace)

    if args.install_shortcut:
        from .shortcuts import install_shortcut

        result = install_shortcut(workspace)
        print(result["message"])
        return 0

    # Already running? Just bring it up in the browser.
    running = _health(args.port)
    if running and not args.share:
        if Path(running.get("workspace", "")) == workspace:
            print(f"Knowledge Keeper is already running at http://127.0.0.1:{args.port}")
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{args.port}")
            return 0

    host = "0.0.0.0" if args.share else "127.0.0.1"
    port = args.port
    while not _port_free(port, host):
        port += 1
        if port > args.port + 50:
            print("No free port found.", file=sys.stderr)
            return 1

    workspace.mkdir(parents=True, exist_ok=True)
    log_path = workspace / "kk_data" / "gui.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    log = logging.getLogger("knowledge_keeper.gui")

    import uvicorn

    from .service import KnowledgeKeeper
    from .web.server import create_app

    access_code = None
    share = {"enabled": False}
    if args.share:
        access_code = secrets.token_urlsafe(6)
        share = {"enabled": True, "url": f"http://{_lan_ip()}:{port}", "code": access_code}

    try:
        service = KnowledgeKeeper(workspace, share=share)
    except Exception:
        log.exception("Knowledge Keeper failed to start")
        raise

    server: Optional[uvicorn.Server] = None

    def stop():
        if server is not None:
            server.should_exit = True

    app = create_app(service, access_code=access_code, on_shutdown=stop)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    local_url = f"http://127.0.0.1:{port}"
    print(f"\n  Knowledge Keeper is running at {local_url}")
    print(f"  Workspace: {workspace}")
    if args.share:
        print(f"  On your network: {share['url']}   access code: {access_code}")
    print("  Close it from the app (Quit in the sidebar) or press Ctrl+C here.\n")
    log.info("started on %s (workspace %s, share=%s)", local_url, workspace, args.share)

    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(local_url, port), daemon=True).start()

    server.run()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
