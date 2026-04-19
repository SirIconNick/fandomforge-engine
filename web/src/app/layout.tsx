import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "FandomForge",
  description: "AI-powered multifandom video creation suite",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#0a0a0a",
};

const THEME_INIT = `try{var t=localStorage.getItem('ff.theme')||'system';var r=t==='system'?(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'):t;document.documentElement.dataset.theme=r;}catch(e){document.documentElement.dataset.theme='dark';}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <head>
        <Script id="theme-init" strategy="beforeInteractive">{THEME_INIT}</Script>
      </head>
      <body className="min-h-screen">
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>
        <Nav />
        <main
          id="main-content"
          className="mx-auto max-w-6xl px-4 sm:px-6 py-6 sm:py-12"
        >
          {children}
        </main>
        <footer
          role="contentinfo"
          className="mx-auto max-w-6xl px-4 sm:px-6 py-6 sm:py-10 text-sm text-[var(--color-ash)] border-t border-white/5 mt-12 sm:mt-20"
        >
          <div className="flex flex-wrap justify-between gap-4">
            <div>FandomForge — built for people who make multifandom edits.</div>
            <div>Local workspace · no data leaves your machine</div>
          </div>
        </footer>
      </body>
    </html>
  );
}
