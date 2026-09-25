import Icon from "../icons.jsx";

export function Toasts({ toasts, onDismiss }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
          <Icon name={t.tone === "error" ? "alert" : t.tone === "success" ? "check" : "sparkle"} size={16} />
          <span>{t.message}</span>
          <button className="toast-x" onClick={() => onDismiss(t.id)} aria-label="Dismiss">
            <Icon name="x" size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
