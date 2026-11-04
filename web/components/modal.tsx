"use client";

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

/** A native modal dialog: focus is trapped, Escape closes it. */
export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className="m-auto w-[min(480px,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-0 text-foreground shadow-[0_24px_60px_rgb(0_0_0/0.22)] backdrop:bg-black/35"
    >
      <div className="flex items-center justify-between border-b border-rule px-5 py-3">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        <button aria-label="Close" onClick={onClose} className="rounded-md p-1 text-muted hover:bg-sunken">
          <X size={16} />
        </button>
      </div>
      <div className="p-5">{open && children}</div>
    </dialog>
  );
}

/** Asks before something that goes to an approver or changes what others see: a slip of the
 * mouse should never submit a plan. */
export function Confirm({
  open,
  title,
  children,
  action,
  busy,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: ReactNode;
  children: ReactNode;
  action: ReactNode;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <div className="flex flex-col gap-4 text-sm">
        <div className="leading-relaxed">{children}</div>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} className="min-h-9 rounded-md border border-control px-3.5 text-sm font-medium hover:bg-sunken">
            Cancel
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onConfirm}
            className="min-h-9 rounded-md bg-accent px-3.5 text-sm font-medium text-white hover:bg-[#184d7d] disabled:opacity-55"
          >
            {action}
          </button>
        </div>
      </div>
    </Modal>
  );
}
