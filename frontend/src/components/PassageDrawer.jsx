import { useEffect, useRef } from "react";
import Icon from "../icons.jsx";
import { formatLocation } from "../format.js";

// Side panel showing the full text and provenance of one retrieved passage.
export default function PassageDrawer({ passage, onClose }) {
  const closeRef = useRef(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const modified = passage.modified_at ? new Date(passage.modified_at).toLocaleDateString() : "unknown";

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={`Source ${passage.n}`}>
        <div className="drawer-head">
          <div>
            <div className="kicker">Source {passage.n} · {formatLocation(passage.location)}</div>
            <h3>{passage.doc_title}</h3>
          </div>
          <button ref={closeRef} className="btn ghost icon-only" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>
        <dl className="drawer-meta">
          <dt>Author</dt>
          <dd>{passage.author || "—"}</dd>
          <dt>Last modified</dt>
          <dd>{modified}</dd>
          <dt>Match score</dt>
          <dd>{passage.score.toFixed(3)}</dd>
        </dl>
        <div className="drawer-body">{passage.text}</div>
      </aside>
    </>
  );
}
