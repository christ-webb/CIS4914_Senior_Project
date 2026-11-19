"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { checkHistoryGeneration } from "@/lib/api";

import { useEffect, useRef, useState, type ReactNode } from "react";

const NAV = [
  { href: "/", label: "Dashboard", icon: "M3 13h8V3H3v10Zm0 8h8v-6H3v6Zm10 0h8V11h-8v10Zm0-18v6h8V3h-8Z" },
  { href: "/explorer", label: "Explorer", icon: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13ZM20 20l-4.8-4.8" },
  { href: "/analytics", label: "Analytics", icon: "M4 20V10m6 10V4m6 16v-7m5 7H2" },
  { href: "/errors", label: "Errors", icon: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" },
];

function PanelIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" data-icon={open ? "panel-left-close" : "panel-left-open"}>
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M9 3v18" />
      <path className="panel-chevron" d={open ? "m16 15-3-3 3-3" : "m14 9 3 3-3 3"} />
    </svg>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  useEffect(() => {
    const check = () => { void checkHistoryGeneration().catch(() => undefined); };
    check();
    const timer = setInterval(check, 5000);
    window.addEventListener("focus", check);
    return () => { clearInterval(timer); window.removeEventListener("focus", check); };
  }, []);
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" || pathname.startsWith("/traces") : pathname.startsWith(href);

  const [collapsed, setCollapsed] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [mobile, setMobile] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const aside = useRef<HTMLElement>(null);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 860px)");
    const resize = () => { setMobile(media.matches); setDrawer(false); };
    media.addEventListener("change", resize);
    const saved = sessionStorage.getItem("nm5-sidebar-collapsed") === "true";
    const timer = setTimeout(() => { setCollapsed(saved); setMobile(media.matches); }, 0);
    return () => { clearTimeout(timer); media.removeEventListener("change", resize); };
  }, []);
  useEffect(() => {
    if (!drawer) return;
    aside.current?.querySelector<HTMLAnchorElement>("nav a")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setDrawer(false); toggle.current?.focus(); }
      if (event.key === "Tab") {
        const links = Array.from(aside.current?.querySelectorAll<HTMLAnchorElement>("a") ?? []);
        const first = links[0], last = links.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [drawer]);
  function onToggle() {
    if (window.matchMedia("(max-width: 860px)").matches) setDrawer(!drawer);
    else { sessionStorage.setItem("nm5-sidebar-collapsed", String(!collapsed)); setCollapsed(!collapsed); }
  }
  const expanded = mobile ? drawer : !collapsed;
  return (
    <div className={`shell ${collapsed ? "sidebar-collapsed" : ""} ${drawer ? "drawer-open" : ""}`}>
      <button ref={toggle} className="sidebar-toggle" aria-label="Toggle navigation" title={expanded ? "Collapse navigation" : "Expand navigation"} aria-controls="main-navigation" aria-expanded={expanded} onClick={onToggle}>
        <PanelIcon open={expanded} />
      </button>
      {drawer && <button className="drawer-backdrop" aria-label="Close navigation" onClick={() => { setDrawer(false); toggle.current?.focus(); }} />}
      <aside ref={aside} className="sidebar" id="main-navigation" aria-label="Navigation">
        <Link href="/" className="brand" aria-label="No More 500s home" onClick={() => setDrawer(false)}>
          <span className="brand-mark" aria-hidden="true"><span>5</span></span>
          <span className="brand-word nav-label">NO MORE <b>500s</b></span>
        </Link>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <Link key={item.href} href={item.href} title={item.label} aria-label={item.label} onClick={() => setDrawer(false)} className={isActive(item.href) ? "nav-link active" : "nav-link"} aria-current={isActive(item.href) ? "page" : undefined}>
              <svg className="nav-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={item.icon} /></svg>
              <span className="nav-label">{item.label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar-foot" aria-hidden="true"><i /><i /><i /></div>
      </aside>
      <main inert={drawer}>{children}</main>
    </div>
  );
}
