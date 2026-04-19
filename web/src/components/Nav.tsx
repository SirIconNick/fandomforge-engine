"use client";

import Link from "next/link";
import { useState } from "react";
import ThemeToggle from "@/components/ThemeToggle";

const links = [
  { href: "/", label: "Home" },
  { href: "/projects", label: "Projects" },
  { href: "/pipeline", label: "Pipeline" },
  { href: "/experts", label: "Experts" },
  { href: "/experts/council", label: "Council" },
  { href: "/knowledge", label: "Knowledge" },
  { href: "/beat-map", label: "Beat Map" },
  { href: "/usage", label: "Usage" },
];

export function Nav() {
  const [open, setOpen] = useState(false);

  return (
    <nav
      aria-label="Main navigation"
      className="border-b border-white/5 bg-[var(--color-ink)]/80 backdrop-blur sticky top-0 z-50"
    >
      <div className="mx-auto max-w-6xl px-4 sm:px-6 py-4 flex items-center justify-between gap-3">
        <Link href="/" className="flex items-center gap-3 group" aria-label="FandomForge home">
          <span className="relative flex h-8 w-8 items-center justify-center rounded bg-[var(--color-forge)] text-[var(--color-ink)] font-bold" aria-hidden="true">
            FF
          </span>
          <span className="font-display text-xl tracking-tight">FandomForge</span>
        </Link>

        {/* Desktop links */}
        <div className="hidden md:flex gap-1 text-sm items-center">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="px-3 py-1.5 rounded hover:bg-white/5 transition-colors text-[var(--color-mist)] hover:text-[var(--color-paper)]"
            >
              {l.label}
            </Link>
          ))}
          <ThemeToggle />
        </div>

        {/* Mobile hamburger */}
        <div className="flex md:hidden items-center gap-2">
          <ThemeToggle />
          <button
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? "Close navigation menu" : "Open navigation menu"}
            aria-expanded={open}
            aria-controls="mobile-nav-drawer"
            className="flex flex-col gap-1 p-2 rounded border border-white/10 hover:border-white/30"
          >
            <span
              className={`block h-0.5 w-5 bg-[var(--color-paper)] transition-transform ${
                open ? "translate-y-1.5 rotate-45" : ""
              }`}
              aria-hidden="true"
            />
            <span
              className={`block h-0.5 w-5 bg-[var(--color-paper)] transition-opacity ${
                open ? "opacity-0" : ""
              }`}
              aria-hidden="true"
            />
            <span
              className={`block h-0.5 w-5 bg-[var(--color-paper)] transition-transform ${
                open ? "-translate-y-1.5 -rotate-45" : ""
              }`}
              aria-hidden="true"
            />
          </button>
        </div>
      </div>

      {open && (
        <div
          id="mobile-nav-drawer"
          className="md:hidden border-t border-white/5 bg-[var(--color-ink)]/95"
        >
          <ul className="px-4 py-2 space-y-1">
            {links.map((l) => (
              <li key={l.href}>
                <Link
                  href={l.href}
                  onClick={() => setOpen(false)}
                  className="block px-3 py-2 rounded hover:bg-white/5 text-sm text-[var(--color-mist)] hover:text-[var(--color-paper)]"
                >
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </nav>
  );
}
