"""Tests for the local web app's API (knowledge_keeper.web.server) and service layer.

Runs fully offline against a temporary workspace; no browser or AI model needed.
"""
from __future__ import annotations

import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from knowledge_keeper.service import KnowledgeKeeper, resolve_workspace
from knowledge_keeper.web.server import create_app

H = {"X-KK-Client": "1"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KK_KEYSTORE_DIR", str(tmp_path / "keys"))
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "KK_PROVIDER", "KK_LLM__PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    cwd = os.getcwd()
    service = KnowledgeKeeper(tmp_path / "ws")
    client = TestClient(create_app(service), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    yield service, client
    os.chdir(cwd)


def wait(client, job):
    for _ in range(200):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def load_demo(client):
    j = wait(client, client.post("/api/documents/demo", headers=H).json()["job"])
    assert j["status"] == "done", j
    return j


def test_status_and_index(env):
    _, client = env
    s = client.get("/api/status").json()
    assert s["provider"] == "local" and s["document_count"] == 0 and s["llm"]["provider"] == "none"
    page = client.get("/")
    assert page.status_code == 200 and '<div id="root">' in page.text, "React build missing from web/static"


def test_guards(env):
    service, client = env
    # state-changing request without the client header
    assert client.post("/api/documents/demo").status_code == 403
    # DNS-rebinding style Host header
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 403
    # machine-level actions refuse non-local clients
    remote = TestClient(create_app(service, access_code="secret"), base_url="http://127.0.0.1",
                        client=("192.168.1.50", 5000))
    assert remote.get("/api/status").status_code == 401
    assert remote.get("/api/status?code=wrong", follow_redirects=False).status_code == 401
    assert remote.get("/?code=secret", follow_redirects=False).status_code == 303  # sets the cookie
    remote.cookies.set("kk_access", "secret")
    assert remote.get("/api/status").status_code == 200
    r = remote.put("/api/settings", json={"provider": "local"}, headers=H)
    assert r.status_code == 403


def test_upload_ask_scope_remove(env, tmp_path):
    service, client = env
    load_demo(client)
    note = b"# Team Handbook\n\nPasswords rotate every 90 days. Report incidents to security.\n"
    r = client.post("/api/documents/upload", files={"files": ("handbook.md", note, "text/markdown")}, headers=H)
    assert r.status_code == 200
    assert wait(client, r.json()["job"])["result"]["ingested"] == 1

    docs = client.get("/api/documents").json()
    assert len(docs) == 5
    handbook = next(d for d in docs if d["title"] == "Team Handbook")
    assert handbook["in_library"] and handbook["chunks"] >= 1

    # re-uploading the same file is a no-op
    r = client.post("/api/documents/upload", files={"files": ("handbook.md", note, "text/markdown")}, headers=H)
    assert wait(client, r.json()["job"])["result"]["unchanged"] == 1

    # unsupported type rejected
    r = client.post("/api/documents/upload", files={"files": ("x.exe", b"MZ", "application/octet-stream")}, headers=H)
    assert r.status_code == 400

    # scoped question only searches the chosen document
    ans = client.post("/api/ask", json={"question": "how often do passwords rotate", "doc_ids": [handbook["doc_id"]]},
                      headers=H).json()
    assert ans["mode"] == "passages" and ans["passages"]
    assert {p["doc_title"] for p in ans["passages"]} == {"Team Handbook"}

    # remove deletes the library copy too
    r = client.delete(f"/api/documents/{handbook['doc_id']}", headers=H).json()
    assert r["file_deleted"] is True
    assert len(client.get("/api/documents").json()) == 4
    assert not (service.documents_dir / "handbook.md").exists()


def test_non_western_text_round_trips(env):
    """Text outside Windows' default cp1252 encoding must survive indexing,
    search, analysis, and reports. (This crashed on Windows before every file
    read/write was made explicitly UTF-8.)"""
    _, client = env
    text = "# Ōkami notes — ā ş ł ő 漢字 🙂\n\nThe ledger reconciles nightly — “smart quotes” and emoji 🙂 included.\n"
    r = client.post("/api/documents/upload", files={"files": ("notes.md", text.encode("utf-8"), "text/markdown")}, headers=H)
    assert wait(client, r.json()["job"])["result"]["ingested"] == 1
    docs = client.get("/api/documents").json()
    assert docs[0]["title"] == "Ōkami notes — ā ş ł ő 漢字 🙂"
    ans = client.post("/api/ask", json={"question": "ledger reconciles"}, headers=H).json()
    assert "🙂" in ans["passages"][0]["text"]
    assert wait(client, client.post("/api/analyze", json={"use_llm": False}, headers=H).json()["job"])["status"] == "done"
    assert wait(client, client.post("/api/report", json={"use_llm": False}, headers=H).json()["job"])["status"] == "done"
    assert "漢字" in client.get("/api/report/latest").json()["markdown"]


def test_analyze_and_report(env):
    _, client = env
    assert client.get("/api/analysis").json() is None
    load_demo(client)
    j = wait(client, client.post("/api/analyze", json={"use_llm": True}, headers=H).json()["job"])
    assert j["status"] == "done" and j["result"]["findings"] > 0
    a = client.get("/api/analysis").json()
    assert {f["kind"] for f in a["findings"]} >= {"stale"}
    j = wait(client, client.post("/api/report", json={"use_llm": False}, headers=H).json()["job"])
    assert j["status"] == "done"
    rep = client.get("/api/report/latest").json()
    assert rep["markdown"].startswith("# System Knowledge Report")
    assert client.get("/api/report/download").status_code == 200


def test_one_job_at_a_time(env):
    service, client = env
    import threading

    gate = threading.Event()
    service.jobs.start("test", "Slow task", lambda job: gate.wait(5) and {})
    r = client.post("/api/documents/demo", headers=H)
    assert r.status_code == 409
    gate.set()


def test_settings_and_keys(env):
    service, client = env
    s = client.put("/api/settings", json={"values": {"llm.provider": "gemini", "gemini.model": "gemini-3.6-flash"}},
                   headers=H).json()
    assert s["values"]["llm.provider"] == "gemini"
    st = client.get("/api/status").json()["llm"]
    assert st["ready"] is False and "API key" in st["error"]

    key = client.post("/api/settings/key", json={"name": "GEMINI_API_KEY", "value": "AIzaTEST0000000000", "remember": True},
                      headers=H).json()
    assert key["source"] == "saved" and key["hint"].startswith("AIza") and "TEST0000" not in key["hint"]
    assert client.get("/api/status").json()["llm"]["ready"] is True

    keyfile = service.workspace.parent / "keys" / "keys.env"
    assert keyfile.exists()
    if os.name == "posix":
        assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    # never written into the workspace config
    assert "AIzaTEST" not in (service.workspace / "config.yaml").read_text(encoding="utf-8")

    client.delete("/api/settings/key/GEMINI_API_KEY", headers=H)
    assert "GEMINI_API_KEY" not in keyfile.read_text(encoding="utf-8")
    assert client.post("/api/settings/key", json={"name": "PATH", "value": "x"}, headers=H).status_code == 400
    assert client.put("/api/settings", json={"values": {"llm.provider": "bogus"}}, headers=H).status_code == 400


def test_llm_error_falls_back_to_passages(env, monkeypatch):
    service, client = env
    load_demo(client)

    class Broken:
        on_status = print

        def complete(self, system, user):
            raise RuntimeError("Gemini API error 503: overloaded")

    service._get_components()
    store, emb, _ = service._components
    service._components = (store, emb, Broken())
    ans = client.post("/api/ask", json={"question": "billing queue"}, headers=H).json()
    assert ans["mode"] == "passages" and ans["passages"] and "503" in ans["note"]


def test_resolve_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("KK_HOME", raising=False)
    assert resolve_workspace(str(tmp_path / "x")) == (tmp_path / "x").resolve()
    (tmp_path / "kk_data").mkdir()
    assert resolve_workspace(cwd=tmp_path) == tmp_path.resolve()
    monkeypatch.setenv("KK_HOME", str(tmp_path / "home"))
    assert resolve_workspace() == (tmp_path / "home").resolve()


def test_desktop_entry_quoting():
    from knowledge_keeper.shortcuts import _desktop_quote

    assert _desktop_quote("/usr/bin/python3") == "/usr/bin/python3"
    assert _desktop_quote("/home/me/My Docs") == '"/home/me/My Docs"'
    assert _desktop_quote('a"b$c') == '"a\\"b\\$c"'
