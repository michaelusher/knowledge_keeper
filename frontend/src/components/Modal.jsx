import { useEffect, useRef } from "react";

// Accessible modal built on <dialog>. Renders children as the body.
export default function Modal({ title, children, confirmLabel = "OK", tone = "primary", onConfirm, onCancel, disabled }) {
  const ref = useRef(null);

  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    const onClose = () => onCancel?.();
    d?.addEventListener("cancel", onClose);
    return () => d?.removeEventListener("cancel", onClose);
  }, [onCancel]);

  return (
    <dialog className="dialog" ref={ref} aria-labelledby="modal-title">
      <form
        method="dialog"
        onSubmit={(e) => {
          e.preventDefault();
          onConfirm?.();
        }}
      >
        <h3 id="modal-title">{title}</h3>
        <div className="dialog-body">{children}</div>
        <div className="row gap end">
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className={`btn ${tone}`} disabled={disabled}>
            {confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
