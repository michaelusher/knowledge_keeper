import { useEffect, useMemo, useRef, useState } from "react";
import { post } from "../api.js";
import { useApp } from "../context.js";
import Icon from "../icons.jsx";
import { renderMarkdown } from "../markdown.js";
import PassageDrawer from "../components/PassageDrawer.jsx";
import { formatLocation } from "../format.js";

const SUGGESTIONS = [
  "Summarize what these documents cover",
  "What procedures are described, step by step?",
  "Which topics are mentioned but not explained?",
];

export default function AskView() {
  const { docs, status, thread, setThread, scope, setScope, navigate, toast } = useApp();
  const [question, setQuestion] = useState("");
  const [drawer, setDrawer] = useState(null);
  const threadRef = useRef(null);
  const inputRef = useRef(null);
  const pending = thread.some((m) => m.status === "pending");

  // Drop scope entries for documents that were removed.
  useEffect(() => {
    const ids = new Set(docs.map((d) => d.doc_id));
    if (scope.some((id) => !ids.has(id))) setScope(scope.filter((id) => ids.has(id)));
  }, [docs, scope, setScope]);

  // Keep the newest question at the top of the view with its answer below it.
  useEffect(() => {
    const last = threadRef.current?.querySelector(".exchange:last-of-type");
    last?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [thread.length, thread.at(-1)?.status]);

  const ask = async (text) => {
    const q = (text ?? question).trim();
    if (!q || pending) return;
    const id = Date.now();
    const scopeTitles = scope.map((sid) => docs.find((d) => d.doc_id === sid)?.title).filter(Boolean);
    setThread((t) => [...t, { id, question: q, status: "pending", scopeTitles }]);
    setQuestion("");
    try {
      const result = await post("/api/ask", { question: q, doc_ids: scope.length ? scope : null });
      setThread((t) => t.map((m) => (m.id === id ? { ...m, status: "done", result } : m)));
    } catch (e) {
      setThread((t) => t.map((m) => (m.id === id ? { ...m, status: "error", error: e.message } : m)));
      toast(e.message, "error");
    }
    inputRef.current?.focus();
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  };

  const noDocs = docs.length === 0;

  return (
    <section className="view ask-view">
      <header className="view-head">
        <div>
          <h1>Ask your documents</h1>
          <p className="sub">Answers come only from what you've added, with a citation for every claim.</p>
        </div>
        {!noDocs && <ScopePicker />}
      </header>

      <div className="thread" ref={threadRef}>
        {thread.length === 0 ? (
          noDocs ? (
            <div className="empty-hero">
              <Icon name="file" size={32} />
              <h2>Add documents to get started</h2>
              <p>Drop in PDFs, Word files, PowerPoints, or notes — then ask questions about them here.</p>
              <div className="row gap center">
                <button className="btn primary" onClick={() => navigate("documents")}>
                  <Icon name="upload" /> Add documents
                </button>
              </div>
            </div>
          ) : (
            <div className="empty-hero">
              <Icon name="chat" size={32} />
              <h2>What do you want to know?</h2>
              <p>
                Searching {scope.length ? `${scope.length} selected document${scope.length > 1 ? "s" : ""}` : `all ${docs.length} documents`}.
                {status.llm.provider === "none" && " Without an AI model you'll see the best-matching passages."}
              </p>
              <div className="suggestions">
                {SUGGESTIONS.map((s) => (
                  <button key={s} className="chip" onClick={() => ask(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )
        ) : (
          thread.map((m) => <Exchange key={m.id} message={m} onOpen={setDrawer} />)
        )}
      </div>

      <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          ask();
        }}
      >
        <textarea
          ref={inputRef}
          rows={Math.min(6, Math.max(1, question.split("\n").length))}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKey}
          placeholder={noDocs ? "Add documents first…" : "Ask a question…"}
          aria-label="Question"
          disabled={noDocs}
        />
        <button className="btn primary icon-only" disabled={noDocs || pending || !question.trim()} aria-label="Ask">
          {pending ? <span className="spinner light" /> : <Icon name="send" />}
        </button>
      </form>
      <p className="composer-hint">
        Enter to ask · Shift+Enter for a new line
        {thread.length > 0 && (
          <>
            {" · "}
            <button className="link-btn" onClick={() => setThread([])}>
              Clear conversation
            </button>
          </>
        )}
      </p>
      </div>

      {drawer && <PassageDrawer passage={drawer} onClose={() => setDrawer(null)} />}
    </section>
  );
}

function ScopePicker() {
  const { docs, scope, setScope } = useApp();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const label =
    scope.length === 0
      ? "All documents"
      : scope.length === 1
        ? docs.find((d) => d.doc_id === scope[0])?.title ?? "1 document"
        : `${scope.length} documents`;

  const toggle = (id) => setScope(scope.includes(id) ? scope.filter((x) => x !== id) : [...scope, id]);

  return (
    <div className="scope" ref={ref}>
      <button
        className={`btn ${scope.length ? "scoped" : "ghost"}`}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="true"
      >
        <Icon name="filter" /> <span className="scope-label">Searching: {label}</span>
      </button>
      {open && (
        <div className="popover">
          <div className="pop-head">Search in</div>
          <label className="check">
            <input type="checkbox" checked={scope.length === 0} onChange={() => setScope([])} /> All documents
          </label>
          <div className="scope-list">
            {docs.map((d) => (
              <label className="check" key={d.doc_id}>
                <input type="checkbox" checked={scope.includes(d.doc_id)} onChange={() => toggle(d.doc_id)} />
                <span className="truncate" title={d.filename}>
                  {d.title}
                </span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Exchange({ message, onOpen }) {
  const { navigate } = useApp();
  const [showAll, setShowAll] = useState(false);
  const r = message.result;

  const html = useMemo(
    () => (r?.answer ? renderMarkdown(r.answer, { citations: true }) : ""),
    [r?.answer],
  );

  const passageByN = (n) => r?.passages.find((p) => p.n === Number(n));
  const onAnswerClick = (e) => {
    const btn = e.target.closest("[data-cite]");
    if (btn) {
      const p = passageByN(btn.dataset.cite);
      if (p) onOpen(p);
    }
  };

  const cited = r?.mode === "answer" && r.cited.length ? r.cited.map(passageByN).filter(Boolean) : [];
  const listed = r?.mode === "answer" ? (showAll ? r.passages : cited.length ? cited : r.passages.slice(0, 3)) : r?.passages ?? [];

  return (
    <div className="exchange">
      <div className="q-bubble">
        {message.question}
        {message.scopeTitles?.length > 0 && (
          <div className="q-scope">in {message.scopeTitles.join(", ")}</div>
        )}
      </div>

      {message.status === "pending" && (
        <div className="answer-card pending">
          <span className="spinner" /> Searching your documents…
        </div>
      )}

      {message.status === "error" && (
        <div className="answer-card error">
          <Icon name="alert" /> {message.error}
        </div>
      )}

      {message.status === "done" && (
        <div className="answer-card">
          {r.note && (
            <div className={`note ${r.mode === "answer" ? "" : "warn"}`}>
              <Icon name="alert" size={16} />
              <span>
                {r.note}{" "}
                {/Settings/.test(r.note) && (
                  <button className="link-btn" onClick={() => navigate("settings")}>
                    Open Settings
                  </button>
                )}
              </span>
            </div>
          )}

          {r.mode === "answer" && (
            // eslint-disable-next-line react/no-danger
            <div className="prose answer" onClick={onAnswerClick} dangerouslySetInnerHTML={{ __html: html }} />
          )}

          {listed.length > 0 && (
            <div className="sources">
              <div className="sources-head">
                {r.mode === "answer" ? (showAll ? "All retrieved passages" : "Sources") : "Best-matching passages"}
              </div>
              {r.mode === "answer"
                ? listed.map((p) => (
                    <button key={p.n} className="source" onClick={() => onOpen(p)}>
                      <span className="cite static">{p.n}</span>
                      <span className="source-title">{p.doc_title}</span>
                      <span className="muted source-loc">{formatLocation(p.location)}</span>
                    </button>
                  ))
                : listed.map((p) => (
                    <button key={p.n} className="passage" onClick={() => onOpen(p)}>
                      <div className="passage-meta">
                        <span className="cite static">{p.n}</span>
                        <strong>{p.doc_title}</strong>
                        <span className="muted">{formatLocation(p.location)}</span>
                        <span className="score" title="Match score">
                          {p.score.toFixed(2)}
                        </span>
                      </div>
                      <p className="passage-text">{p.text}</p>
                    </button>
                  ))}
              {r.mode === "answer" && r.passages.length > listed.length && !showAll && (
                <button className="link-btn" onClick={() => setShowAll(true)}>
                  Show all {r.passages.length} retrieved passages
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
