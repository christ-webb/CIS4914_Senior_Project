"use client";
import { useEffect, useLayoutEffect, useId, useRef, useState, type ReactNode } from "react";

/** One focusable trigger; pointer-safe hover and an explicitly pinned click state. */
export function Disclosure({ label, children, value }: { label: string; children: ReactNode; value?: ReactNode }) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const pinned = useRef(false);
  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) { pinned.current = false; setOpen(false); } };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  useLayoutEffect(() => {
    if (!open) return;
    const position = () => {
      if (!root.current || !panel.current) return;
      const rect = root.current.getBoundingClientRect();
      const margin = 16;
      const width = Math.min(370, window.innerWidth - margin * 2);
      const below = window.innerHeight - rect.bottom - margin;
      const above = rect.top - margin;
      const placeBelow = below >= Math.min(320, panel.current.scrollHeight) || below >= above;
      const height = Math.min(560, Math.max(100, placeBelow ? below : above));
      Object.assign(panel.current.style, {
        position: "fixed", width: `${width}px`, maxWidth: `${width}px`, maxHeight: `${height}px`,
        left: `${Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin))}px`, right: "auto",
        top: placeBelow ? `${Math.max(margin, rect.bottom)}px` : "auto",
        bottom: placeBelow ? "auto" : `${Math.max(margin, window.innerHeight - rect.top)}px`,
      });
    };
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [open]);
  return <div ref={root} className="disclosure" onMouseEnter={() => setOpen(true)} onMouseLeave={() => { if (!pinned.current && !root.current?.contains(document.activeElement)) setOpen(false); }} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) { pinned.current = false; setOpen(false); } }} onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); trigger.current?.focus(); pinned.current = false; setOpen(false); } }}>
    <button ref={trigger} type="button" className="disclosure-trigger" aria-expanded={open} aria-controls={id} onFocus={() => setOpen(true)} onClick={() => { pinned.current = !pinned.current; setOpen(pinned.current); }}>
      <span>{label} <span aria-hidden="true" className="info-icon">i</span></span>{value !== undefined && <strong>{value}</strong>}
    </button>
    {open && <div ref={panel} id={id} role="region" aria-label={`${label} details`} className="disclosure-panel">
      <div className="disclosure-head"><strong>{label}</strong><button className="close-button" aria-label={`Close ${label} details`} onClick={() => { trigger.current?.focus(); pinned.current = false; setOpen(false); }}>×</button></div>
      {children}
    </div>}
  </div>;
}
