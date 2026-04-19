import { NextRequest } from "next/server";
import fs from "node:fs";
import path from "node:path";
import { PROJECT_ROOT } from "@/lib/fs";
import { projectExists } from "@/lib/project-context";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const project = url.searchParams.get("project");
  if (!project) {
    return new Response(JSON.stringify({ error: "project param required" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }
  if (!(await projectExists(project))) {
    return new Response(JSON.stringify({ error: "project not found" }), {
      status: 404,
      headers: { "content-type": "application/json" },
    });
  }

  const logPath = path.join(PROJECT_ROOT, "projects", project, ".history", "autopilot.jsonl");
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      let offset = 0;
      let closed = false;

      const sendEvent = (text: string) => {
        controller.enqueue(encoder.encode(`data: ${text}\n\n`));
      };

      if (fs.existsSync(logPath)) {
        const raw = fs.readFileSync(logPath, "utf8");
        offset = raw.length;
        for (const line of raw.split("\n")) {
          if (line.trim()) sendEvent(line);
        }
      }

      const interval = setInterval(() => {
        if (closed) return;
        try {
          if (!fs.existsSync(logPath)) return;
          const stat = fs.statSync(logPath);
          if (stat.size > offset) {
            const fd = fs.openSync(logPath, "r");
            const buf = Buffer.alloc(stat.size - offset);
            fs.readSync(fd, buf, 0, buf.length, offset);
            fs.closeSync(fd);
            offset = stat.size;
            const tail = buf.toString("utf8");
            for (const line of tail.split("\n")) {
              if (line.trim()) sendEvent(line);
            }
          }
        } catch {
          /* swallow read race */
        }
      }, 500);

      req.signal.addEventListener("abort", () => {
        closed = true;
        clearInterval(interval);
        try { controller.close(); } catch {}
      });
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
