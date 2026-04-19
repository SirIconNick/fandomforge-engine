import Link from "next/link";

const links = [
  { href: "/", label: "Home" },
  { href: "/projects", label: "Projects" },
  { href: "/pipeline", label: "Pipeline" },
  { href: "/experts", label: "Experts" },
  { href: "/knowledge", label: "Knowledge" },
  { href: "/beat-map", label: "Beat Map" },
];

export function Nav() {
  return (
    <nav className="border-b border-white/5 bg-[var(--color-ink)]/80 backdrop-blur sticky top-0 z-50">
      <div className="mx-auto max-w-6xl px-6 py-4 flex items-center justify-between">
        <Link href="/" className="flex items-center gap-3 group">
          <span className="relative flex h-8 w-8 items-center justify-center rounded bg-[var(--color-forge)] text-[var(--color-ink)] font-bold">
            FF
          </span>
          <span className="font-display text-xl tracking-tight">FandomForge</span>
        </Link>
        <div className="flex gap-2 text-sm">
          {links.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className="px-3 py-1.5 rounded hover:bg-white/5 transition-colors text-[var(--color-mist)] hover:text-[var(--color-paper)]"
            >
              {l.label}
            </Link>
          ))}
        </div>
      </div>
    </nav>
  );
}
