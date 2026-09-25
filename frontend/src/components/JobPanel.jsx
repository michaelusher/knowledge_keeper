import { useEffect, useRef } from "react";
import { useApp } from "../context.js";
import Icon from "../icons.jsx";

// Shows the running (or just-finished) background job if it's one of `kinds`.
export default function JobPanel({ kinds }) {
  const { job } = useApp();
  const logRef = useRef(null);
  const lines = job?.log?.length ?? 0;

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [lines]);

  if (!job || !kinds.includes(job.kind)) return null;
  const running = job.status === "queued" || job.status === "running";
  const pct = job.progress == null ? null : Math.round(job.progress * 100);

  return (
    <div className={`job ${job.status}`} role="status">
      <div className="job-head">
        {running ? <span className="spinner" /> : <Icon name={job.status === "error" ? "alert" : "check"} />}
        <strong>{job.label}</strong>
        <span className="muted">
          {running ? (pct != null ? `${pct}%` : "working…") : job.status === "error" ? "failed" : "finished"} · {job.elapsed}s
        </span>
      </div>
      <div className="bar">
        <div
          className={`bar-fill${pct == null && running ? " indeterminate" : ""}`}
          style={{ width: `${pct == null ? (running ? 30 : 100) : pct}%` }}
        />
      </div>
      {job.log?.length > 0 && (
        <pre className="job-log" ref={logRef}>
          {job.log.slice(-60).join("\n")}
        </pre>
      )}
    </div>
  );
}
