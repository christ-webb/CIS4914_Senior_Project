"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Dashboard", icon: "M3 13h8V3H3v10Zm0 8h8v-6H3v6Zm10 0h8V11h-8v10Zm0-18v6h8V3h-8Z" },
  { href: "/explorer", label: "Trace Explorer", icon: "M4 6h16M4 12h16M4 18h10" },
  { href: "/analytics", label: "Analytics", icon: "M4 20V10m6 10V4m6 16v-7m5 7H2" },
  { href: "/errors", label: "Errors", icon: "M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" || pathname.startsWith("/traces") : pathname.startsWith(href);

  return (
    <div className="shell">
      <aside className="sidebar">
        <Link href="/" className="brand">
          <span className="logo" aria-hidden>5</span>
          <span>No More 500s</span>
        </Link>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <Link key={item.href} href={item.href} className={isActive(item.href) ? "nav-link active" : "nav-link"} aria-current={isActive(item.href) ? "page" : undefined}>
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d={item.icon} />
              </svg>
              {item.label}
            </Link>
          ))}
        </nav>
        <p className="sidebar-foot muted">Agent observability</p>
      </aside>
      <main>{children}</main>
    </div>
  );
}
