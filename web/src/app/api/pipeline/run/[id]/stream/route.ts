import { NextResponse } from "next/server";
import { getRun, streamRunLog } from "@/lib/runner";

type Params = Promise<{ id: string }>;

export async function GET(_req: Request, { params }: { params: Params }) {
  const { id } = await params;
  const run = getRun(id);
  if (!run) {
    return NextResponse.json({ error: "Run not found" }, { status: 404 });
  }

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const sendEvent = (event: string, data: unknown) => {
        controller.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
        );
      };

      sendEvent("start", { runId: id, project: run.project });

      try {
        for await (const chunk of streamRunLog(id)) {
          for (const line of chunk.split("\n")) {
            if (line.trim()) {
              sendEvent("log", { line });
            }
          }
        }
        const final = getRun(id);
        sendEvent("done", {
          status: final?.status ?? "unknown",
          exitCode: final?.exitCode,
          finishedAt: final?.finishedAt,
        });
      } catch (err) {
        sendEvent("error", { message: String(err) });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
