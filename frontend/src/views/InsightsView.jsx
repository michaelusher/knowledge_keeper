import { useCallback, useEffect, useMemo, useState } from "react";
import { get, post } from "../api.js";
import { useApp } from "../context.js";
import Icon from "../icons.jsx";
import JobPanel from "../components/JobPanel.jsx";
import { renderMarkdown } from "../markdown.js";

const KIND_LABEL = {
  gap: "Coverage gap",
  bus_factor: "Single author",
  stale: "Stale",
  conflict: "Contradiction",
  orphan: "No author",
};
const SEVERITIES = ["high", "medium", "low"];

export default function InsightsView() {
  const { status, docs, busy, job, runJob, toast, navigate, isLocal } = useApp();
  const [tab, setTab] = useState("findings");
  const [analysis, setAnalysis] = useState(undefined); // undefined = loading, null = none yet
  const [report, setReport] = useState(null);
  const [useAi, setUseAi] = useState(true);
  const [kindFilter, setKindFilter] = useState(null);

  const aiReady = status.llm.ready;

  const loadAnalysis = useCallback(async () => {
    try {
      setAnalysis(await get("/api/analysis"));
    } catch (e) {
      toast(e.message, "error");
    }
  }, [toast]);

  const loadReport = useCallback(async () => {
    try {
      setReport(await get("/api/report/latest"));
    } catch (e) {
      toast(e.message, "error");
    }
  }, [toast]);

  useEffect(() => {
    loadAnalysis();
    loadReport();
  }, [loadAnalysis, loadReport, status.provider, status.document_count]);

  const analyze = () =>
    runJob(
      () => post("/api/analyze", { use_llm: useAi && aiReady }),
      async (j) => {
        if (j.status !== "done") return;
        await loadAnalysis();
        toast(`Analysis finished: ${j.result.findings} findings across ${j.result.topics} topics.`, "success");
      },
    );

  const makeReport = () =>
    runJob(
      () => post("/api/report", { use_llm: useAi && aiReady }),
      async (j) => {
        if (j.status !== "done") return;
        await Promise.all([loadReport(), loadAnalysis()]);
        setTab("report");
        toast("Report ready.", "success");
      },
    );

  const counts = useMemo(() => {
    const c = { high: 0, medium: 0, low: 0 };
    for (const f of analysis?.findings ?? []) c[f.severity] = (c[f.severity] || 0) + 1;
    return c;
  }, [analysis]);

  const kinds = useMemo(() => {
    const k = {};
    for (const f of analysis?.findings ?? []) k[f.kind] = (k[f.kind] || 0) + 1;
    return k;
  }, [analysis]);

  const findings = (analysis?.findings ?? []).filter((f) => !kindFilter || f.kind === kindFilter);
  const running = busy && ["analyze", "report"].includes(job?.kind);

  if (docs.length === 0) {
    return (
      <section className="view">
        <Head />
        <div className="empty-hero">
          <Icon name="insight" size={32} />
          <h2>Nothing to analyze yet</h2>
          <p>Add documents first. The analysis compares them against each other, so several related files work best.</p>
          <button className="btn primary" onClick={() => navigate("documents")}>
            <Icon name="upload" /> Add documents
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="view">
      <Head>
        <label className={`switch${aiReady ? "" : " disabled"}`} title={aiReady ? "" : "Connect an AI model in Settings"}>
          <input type="checkbox" checked={useAi && aiReady} disabled={!aiReady} onChange={(e) => setUseAi(e.target.checked)} />
          <span className="track" />
          <span>Use AI model</span>
        </label>
        <button className="btn primary" onClick={analyze} disabled={busy}>
          <Icon name="play" /> {analysis ? "Run again" : "Run analysis"}
        </button>
      </Head>

      {!aiReady && (
        <div className="note">
          <Icon name="sparkle" size={16} />
          <span>
            Running with built-in heuristics. An AI model adds real topic extraction, contradiction detection, and written
            report sections.{" "}
            <button className="link-btn" onClick={() => navigate("settings")}>
              Connect one in Settings
            </button>
          </span>
        </div>
      )}

      <JobPanel kinds={["analyze", "report"]} />

      <div className="tabs" role="tablist">
        {[
          ["findings", `Findings${analysis ? ` (${analysis.findings.length})` : ""}`],
          ["topics", `Topics${analysis ? ` (${analysis.topics.length})` : ""}`],
          ["report", "Report"],
        ].map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={`tab${tab === id ? " active" : ""}`} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </div>

      {tab !== "report" && analysis === null && !running && (
        <div className="empty">
          <p>
            <strong>No analysis yet.</strong> Run it to map what your documents cover and flag gaps, single-author topics,
            stale material{aiReady ? ", and contradictions" : ""}.
          </p>
        </div>
      )}

      {tab === "findings" && analysis && (
        <>
          <div className="stats">
            {SEVERITIES.map((s) => (
              <div key={s} className={`stat ${s}`}>
                <div className="stat-num">{counts[s]}</div>
                <div className="stat-label">{s} severity</div>
              </div>
            ))}
            <div className="stat">
              <div className="stat-num">{analysis.document_count}</div>
              <div className="stat-label">documents analyzed</div>
            </div>
          </div>
          {Object.keys(kinds).length > 1 && (
            <div className="chips">
              <button className={`chip${!kindFilter ? " on" : ""}`} onClick={() => setKindFilter(null)}>
                All
              </button>
              {Object.entries(kinds).map(([k, n]) => (
                <button key={k} className={`chip${kindFilter === k ? " on" : ""}`} onClick={() => setKindFilter(k)}>
                  {KIND_LABEL[k] || k} · {n}
                </button>
              ))}
            </div>
          )}
          {findings.length === 0 ? (
            <div className="empty">
              <p>
                <strong>No findings.</strong>{" "}
                {analysis.document_count < 2
                  ? "Most checks compare documents against each other — add related documents for richer results."
                  : "Nothing stood out in this knowledge base."}
              </p>
            </div>
          ) : (
            <div className="findings">
              {findings.map((f, i) => (
                <article key={i} className={`finding ${f.severity}`}>
                  <div className="finding-head">
                    <span className={`sev ${f.severity}`}>{f.severity}</span>
                    <span className="badge">{KIND_LABEL[f.kind] || f.kind}</span>
                  </div>
                  <h3>{f.title}</h3>
                  <p>{f.detail}</p>
                  {f.doc_titles?.length > 0 && (
                    <div className="related">
                      <Icon name="file" size={14} /> {[...new Set(f.doc_titles)].join(" · ")}
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}
          <p className="muted small">Last analyzed {new Date(analysis.generated_at).toLocaleString()}.</p>
        </>
      )}

      {tab === "topics" && analysis && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Kind</th>
                <th>Documented</th>
                <th className="num">Docs</th>
                <th>Authors</th>
                <th>Freshest</th>
              </tr>
            </thead>
            <tbody>
              {analysis.topics.map((t, i) => (
                <tr key={i}>
                  <td>
                    <div className="doc-title">{t.topic}</div>
                    {t.summary && <div className="muted small">{t.summary}</div>}
                  </td>
                  <td>
                    <span className="badge">{t.kind}</span>
                  </td>
                  <td>{t.explained ? "Yes" : <span className="warn-text">Mentioned only</span>}</td>
                  <td className="num">{t.doc_ids.length}</td>
                  <td>{t.authors.join(", ") || <span className="muted">—</span>}</td>
                  <td>{t.newest_modified ? new Date(t.newest_modified).toLocaleDateString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "report" && (
        <>
          <div className="toolbar">
            <button className="btn primary" onClick={makeReport} disabled={busy}>
              <Icon name="docPlus" /> {report ? "Generate new report" : "Generate report"}
            </button>
            {report && (
              <a className="btn" href="/api/report/download">
                <Icon name="download" /> Download .md
              </a>
            )}
            {report && isLocal && (
              <button className="btn" onClick={() => post("/api/open-folder", { which: "reports" }).catch((e) => toast(e.message, "error"))}>
                <Icon name="external" /> Open reports folder
              </button>
            )}
            {report && <span className="muted small">{report.name}</span>}
          </div>
          {report ? (
            <ReportBody markdown={report.markdown} />
          ) : (
            !running && (
              <div className="empty">
                <p>
                  <strong>No report yet.</strong> A report collects the document inventory, topic coverage
                  {aiReady ? ", written documentation for each topic," : ""} and every finding into one Markdown file you can
                  share.
                </p>
              </div>
            )
          )}
        </>
      )}
    </section>
  );
}

function Head({ children }) {
  return (
    <header className="view-head">
      <div>
        <h1>Insights</h1>
        <p className="sub">Audit the whole knowledge base: undocumented topics, single-author risks, stale docs, and contradictions.</p>
      </div>
      {children && <div className="head-actions">{children}</div>}
    </header>
  );
}

function ReportBody({ markdown }) {
  const html = useMemo(() => renderMarkdown(markdown.replace(/^# .*\n/, "")), [markdown]);
  const title = markdown.match(/^# (.*)$/m)?.[1] ?? "Report";
  return (
    <article className="prose report">
      <h2 className="report-title">{title}</h2>
      {/* eslint-disable-next-line react/no-danger */}
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </article>
  );
}
