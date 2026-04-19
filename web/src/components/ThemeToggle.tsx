"use client";

import { useEffect, useState } from "react";

type Theme = "dark" | "light" | "system";

const STORAGE_KEY = "ff.theme";

function applyTheme(theme: Theme) {
  if (typeof document === "undefined") return;
  const resolved =
    theme === "system"
      ? window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark"
      : theme;
  document.documentElement.dataset.theme = resolved;
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const stored = (window.localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "system";
      setTheme(stored);
      applyTheme(stored);
    } catch {
      applyTheme("system");
    }

    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      if ((window.localStorage.getItem(STORAGE_KEY) ?? "system") === "system") {
        applyTheme("system");
      }
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  function cycle() {
    const next: Theme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
    setTheme(next);
    try { window.localStorage.setItem(STORAGE_KEY, next); } catch {}
    applyTheme(next);
  }

  const label = theme === "system" ? "system" : theme === "light" ? "light" : "dark";
  const icon = theme === "system" ? "◑" : theme === "light" ? "☼" : "☾";

  return (
    <button
      onClick={cycle}
      aria-label={`Theme: ${label} (click to cycle)`}
      title={`Theme: ${label} (click to cycle)`}
      className="px-2 py-1 rounded border border-white/10 text-xs text-[var(--color-mist)] hover:border-white/30 hover:text-[var(--color-paper)]"
    >
      <span aria-hidden="true" className="mr-1">{icon}</span>
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}
