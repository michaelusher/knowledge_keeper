import { useCallback, useEffect, useRef, useState } from "react";
import { get, post } from "./api.js";
import Icon from "./icons.jsx";
import AskView from "./views/AskView.jsx";
import DocumentsView from "./views/DocumentsView.jsx";
import InsightsView from "./views/InsightsView.jsx";
import SettingsView from "./views/SettingsView.jsx";
import { Toasts } from "./components/Toasts.jsx";
import { AppContext } from "./context.js";

const VIEWS = [
  { id: "ask", label: "Ask", icon: "chat" },
  { id: "documents", label: "Documents", icon: "file" },
  { id: "insights", label: "Insights", icon: "insight" },
  { id: "settings", label: "Settings", icon: "sliders" },
];

const viewFromHash = () => {
  const v = window.location.hash.replace(/^#\/?/, "");
  return VIEWS.some((x) => x.id === v) ? v : "ask";
};

export default function App() {
  const [view, setView] = useState(viewFromHash);
  const [status, setStatus] = useState(null);
  const [docs, setDocs] = useState([]);
  const [job, setJob] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [offline, setOffline] = useState(false);
  const [stopped, setStopped] = useState(false);
  // Conversation + scope live here so they survive switching views.
  const [thread, setThread] = useState([]);
  const [scope, setScope] = useState([]); // [] = all documents
  const jobCallbacks = useRef({});

  const toast = useCallback((message, tone = "info") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t.slice(-2), { id, message, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "error" ? 9000 : 4500);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([get("/api/status"), get("/api/documents")]);
      setStatus(s);
      setDocs(d);
      setOffline(false);
      return s;
    } catch {
      setOffline(true);
      return null;
    }
  }, []);

  // Poll a background job until it finishes, then refresh everything.
  const followJob = useCallback(
    (jobData, onDone) => {
      setJob(jobData);
      if (onDone) jobCallbacks.current[jobData.id] = onDone;
      const tick = async () => {
        let j;
        try {
          j = await get(`/api/jobs/${jobData.id}`);
        } catch {
          setTimeout(tick, 1500);
          return;
        }
        setJob(j);
        if (j.status === "queued" || j.status === "running") {
          setTimeout(tick, 700);
          return;
        }
        await refresh();
        const cb = jobCallbacks.current[j.id];
        delete jobCallbacks.current[j.id];
        if (j.status === "error") toast(j.error || "Something went wrong.", "error");
        cb?.(j);
      };
      setTimeout(tick, 400);
    },
    [refresh, toast],
  );

  // Start a job-returning request: runJob(() => post("/api/analyze", {...}), onDone)
  const runJob = useCallback(
    async (request, onDone) => {
      try {
        const res = await request();
        followJob(res.job, onDone);
        return res;
      } catch (e) {
        toast(e.message, "error");
        return null;
      }
    },
    [followJob, toast],
  );

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    refresh().then((s) => {
      if (s?.job) followJob(s.job); // resume a job started before a page reload
      if (s && s.document_count === 0 && !window.location.hash) navigate("documents");
    });
    const t = setInterval(() => {
      if (!stopped) refresh();
    }, 8000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, followJob]);

  const navigate = (id) => {
    window.location.hash = `/${id}`;
    setView(id);
  };

  const askAbout = (doc) => {
    setScope([doc.doc_id]);
    navigate("ask");
  };

  const quit = async () => {
    try {
      await post("/api/shutdown");
    } catch {
      /* server may close before replying */
    }
    setStopped(true);
  };

  const busy = !!job && (job.status === "queued" || job.status === "running");
  const ctx = {
    status, docs, refresh, toast, job, busy, runJob, navigate, askAbout,
    thread, setThread, scope, setScope,
    isLocal: status?.is_local_client ?? true,
  };

  if (stopped) {
    return (
      <div className="stopped">
        <img src="/favicon.svg" alt="" width="48" height="48" />
        <h1>Knowledge Keeper has stopped</h1>
        <p>You can close this tab. Open the app again from your app menu or with <code>kk-gui</code>.</p>
      </div>
    );
  }

  return (
    <AppContext.Provider value={ctx}>
      <div className="app">
        <aside className="sidebar">
          <div className="brand">
            <img src="/favicon.svg" alt="" width="28" height="28" />
            <span>Knowledge Keeper</span>
          </div>
          <nav className="nav" aria-label="Main">
            {VIEWS.map((v) => (
              <button
                key={v.id}
                className={`nav-item${view === v.id ? " active" : ""}`}
                aria-current={view === v.id ? "page" : undefined}
                onClick={() => navigate(v.id)}
              >
                <Icon name={v.icon} />
                <span>{v.label}</span>
                {v.id === "documents" && docs.length > 0 && <span className="nav-count">{docs.length}</span>}
                {v.id === "insights" && busy && ["analyze", "report"].includes(job.kind) && <span className="dot-busy" />}
              </button>
            ))}
          </nav>
          <StatusFoot status={status} offline={offline} onQuit={ctx.isLocal ? quit : null} navigate={navigate} />
        </aside>

        <main className="main">
          {offline && (
            <div className="banner error">
              <Icon name="alert" /> Lost connection to Knowledge Keeper. If you closed it, start it again from your app menu.
            </div>
          )}
          {!status ? (
            <div className="loading">Loading…</div>
          ) : view === "ask" ? (
            <AskView />
          ) : view === "documents" ? (
            <DocumentsView />
          ) : view === "insights" ? (
            <InsightsView />
          ) : (
            <SettingsView />
          )}
        </main>
      </div>
      <Toasts toasts={toasts} onDismiss={(id) => setToasts((t) => t.filter((x) => x.id !== id))} />
    </AppContext.Provider>
  );
}

const SHORT_BACKEND = { local: "Stored on this computer", azure: "Stored in Azure", aws: "Stored in AWS" };

function StatusFoot({ status, offline, onQuit, navigate }) {
  if (!status) return <div className="sidebar-foot" />;
  const backend = status.backends.find((b) => b.name === status.provider);
  const llm = status.llm;
  const aiLabel =
    llm.provider === "none" ? "No AI model" : llm.ready ? `${llm.model || llm.provider}` : "AI model needs setup";
  return (
    <div className="sidebar-foot">
      <button className="status-line" onClick={() => navigate("settings")} title="Storage backend">
        <Icon name={status.provider === "local" ? "laptop" : "cloud"} size={15} />
        <span>{SHORT_BACKEND[status.provider] ?? backend?.label ?? status.provider}</span>
      </button>
      <button className="status-line" onClick={() => navigate("settings")} title="AI model">
        <span className={`pip ${offline ? "bad" : llm.provider === "none" ? "off" : llm.ready ? "ok" : "warn"}`} />
        <span>{aiLabel}</span>
      </button>
      {status.share?.enabled && (
        <div className="status-line" title="Shared on your network">
          <Icon name="share" size={15} /> <span>Shared on network</span>
        </div>
      )}
      {onQuit && (
        <button className="link-btn quit" onClick={onQuit}>
          <Icon name="power" size={15} /> Quit
        </button>
      )}
    </div>
  );
}
