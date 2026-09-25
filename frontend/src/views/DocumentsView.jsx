import { useEffect, useRef, useState } from "react";
import { del, post, upload } from "../api.js";
import { useApp } from "../context.js";
import Icon from "../icons.jsx";
import JobPanel from "../components/JobPanel.jsx";
import Modal from "../components/Modal.jsx";

const TYPE_LABEL = { pdf: "PDF", docx: "Word", pptx: "PowerPoint", markdown: "Markdown", text: "Text" };

export default function DocumentsView() {
  const { docs, status, busy, runJob, toast, askAbout, isLocal, refresh } = useApp();
  const fileRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(null);
  const [folderOpen, setFolderOpen] = useState(false);
  const [folderPath, setFolderPath] = useState("");
  const accept = status.supported_extensions.join(",");

  const addFiles = (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (busy) {
      toast("Please wait for the current task to finish.", "error");
      return;
    }
    runJob(
      async () => {
        const res = await upload(files);
        for (const [name, why] of Object.entries(res.rejected || {})) toast(`${name}: ${why}`, "error");
        return res;
      },
      (j) => j.status === "done" && summarize(j.result),
    );
  };

  const summarize = (r) => {
    if (!r) return;
    const parts = [];
    if (r.ingested) parts.push(`${r.ingested} added`);
    if (r.unchanged) parts.push(`${r.unchanged} already indexed`);
    if (r.failed) parts.push(`${r.failed} failed`);
    toast(parts.join(", ") || "Nothing new to add.", r.failed ? "error" : "success");
  };

  // Drag files anywhere onto the window.
  useEffect(() => {
    let depth = 0;
    const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes("Files");
    const enter = (e) => {
      if (!hasFiles(e)) return;
      depth++;
      setDragging(true);
    };
    const leave = (e) => {
      if (!hasFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) setDragging(false);
    };
    const over = (e) => hasFiles(e) && e.preventDefault();
    const drop = (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      addFiles(e.dataTransfer.files);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  });

  const remove = async (doc) => {
    setConfirmRemove(null);
    try {
      const r = await del(`/api/documents/${doc.doc_id}`);
      toast(`Removed “${r.removed}”.`, "success");
      refresh();
    } catch (e) {
      toast(e.message, "error");
    }
  };

  const importFolder = () => {
    const path = folderPath.trim();
    if (!path) return;
    setFolderOpen(false);
    runJob(() => post("/api/documents/import-folder", { path }), (j) => j.status === "done" && summarize(j.result));
  };

  const openFolder = (which) => post("/api/open-folder", { which }).catch((e) => toast(e.message, "error"));

  return (
    <section className="view">
      <header className="view-head">
        <div>
          <h1>Documents</h1>
          <p className="sub">
            PDF, Word, PowerPoint, Markdown, and text files.{" "}
            {status.provider === "local"
              ? "Everything stays on this computer."
              : `Stored in your ${status.provider.toUpperCase()} knowledge base.`}
          </p>
        </div>
        <div className="head-actions">
          <button className="btn primary" onClick={() => fileRef.current?.click()} disabled={busy}>
            <Icon name="upload" /> Add files
          </button>
        </div>
      </header>

      <input
        ref={fileRef}
        type="file"
        multiple
        hidden
        accept={accept}
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = "";
        }}
      />

      <button
        className={`dropzone${docs.length ? " compact" : ""}`}
        onClick={() => fileRef.current?.click()}
        disabled={busy}
      >
        <Icon name="upload" size={docs.length ? 18 : 28} />
        <strong>Drop files here</strong>
        <span>or click to choose · {status.supported_extensions.join(" ")}</span>
      </button>

      <JobPanel kinds={["ingest"]} />

      <div className="toolbar">
        {isLocal && (
          <button className="btn" onClick={() => setFolderOpen(true)} disabled={busy}>
            <Icon name="folder" /> Import a folder…
          </button>
        )}
        <button
          className="btn"
          disabled={busy}
          onClick={() => runJob(() => post("/api/documents/rescan"), (j) => j.status === "done" && summarize(j.result))}
          title="Index anything you copied into the documents folder yourself"
        >
          <Icon name="refresh" /> Scan documents folder
        </button>
        {isLocal && (
          <button className="btn" onClick={() => openFolder("documents")}>
            <Icon name="external" /> Open documents folder
          </button>
        )}
        <button
          className="btn ghost"
          disabled={busy}
          onClick={() => runJob(() => post("/api/documents/demo"), (j) => j.status === "done" && summarize(j.result))}
          title="Four sample files with planted problems for the analysis to find"
        >
          <Icon name="sparkle" /> Load demo documents
        </button>
      </div>

      {docs.length === 0 ? (
        <div className="empty">
          <p>
            <strong>No documents yet.</strong> Add files above, or load the demo set to see what the analysis finds.
          </p>
        </div>
      ) : (
        <div className="table-wrap">
          <table className="table docs">
            <thead>
              <tr>
                <th>Document</th>
                <th>Type</th>
                <th>Author</th>
                <th>Modified</th>
                <th className="num">Passages</th>
                <th className="actions">
                  <span className="sr">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.doc_id}>
                  <td>
                    <div className="doc-title">{d.title}</div>
                    <div className="muted small truncate" title={d.path}>
                      {d.filename}
                      {!d.in_library && " · imported in place"}
                      {!d.file_exists && " · original file missing"}
                    </div>
                  </td>
                  <td>
                    <span className="badge">{TYPE_LABEL[d.type] || d.type}</span>
                  </td>
                  <td>{d.author || <span className="muted">—</span>}</td>
                  <td>{d.modified_at ? new Date(d.modified_at).toLocaleDateString() : "—"}</td>
                  <td className="num">
                    {d.chunks ?? "—"}
                    {d.chunks === 0 && (
                      <span className="warn-text" title="No text was found. Scanned PDFs need OCR first.">
                        {" "}
                        <Icon name="alert" size={14} />
                      </span>
                    )}
                  </td>
                  <td className="actions">
                    <button className="btn ghost small" onClick={() => askAbout(d)} title="Ask questions about just this document">
                      <Icon name="chat" size={16} /> Ask
                    </button>
                    <button
                      className="btn ghost icon-only small danger"
                      onClick={() => setConfirmRemove(d)}
                      aria-label={`Remove ${d.title}`}
                      title="Remove from knowledge base"
                      disabled={busy}
                    >
                      <Icon name="trash" size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {dragging && (
        <div className="drag-veil">
          <div>
            <Icon name="upload" size={32} />
            Drop to add to your knowledge base
          </div>
        </div>
      )}

      {confirmRemove && (
        <Modal
          title={`Remove “${confirmRemove.title}”?`}
          confirmLabel="Remove"
          tone="danger"
          onConfirm={() => remove(confirmRemove)}
          onCancel={() => setConfirmRemove(null)}
        >
          <p>
            It will no longer be searched or analyzed.
            {confirmRemove.in_library
              ? " The copy in your documents folder is deleted too."
              : " The original file on your computer is not touched."}
          </p>
        </Modal>
      )}

      {folderOpen && (
        <Modal
          title="Import a folder"
          confirmLabel="Import"
          onConfirm={importFolder}
          onCancel={() => setFolderOpen(false)}
          disabled={!folderPath.trim()}
        >
          <p className="muted">
            Every supported file inside is indexed where it is — nothing is copied. Paste the folder's full path, for
            example <code>{status.platform === "windows" ? "C:\\Users\\you\\Documents\\training-docs" : "~/Documents/training-docs"}</code>.
          </p>
          <input
            autoFocus
            className="input"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            placeholder="Folder path"
            aria-label="Folder path"
            spellCheck={false}
          />
        </Modal>
      )}
    </section>
  );
}
