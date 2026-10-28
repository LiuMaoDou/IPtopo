import type { ChatContext, ChatStreamEvent } from "./types";

/** SSE transport shared by the panel and tests. Frames may span any network chunk. */
export function createEventParser(onEvent: (event: ChatStreamEvent) => void) {
  let buffer = "";
  const emit = (frame: string) => {
    const data = frame.split(/\r\n|\r|\n/).filter(line => line.startsWith("data:")).map(line => line.slice(5).replace(/^ /, "")).join("\n");
    if (!data || data === "[DONE]") return;
    const event: unknown = JSON.parse(data);
    if (!event || typeof event !== "object" || typeof (event as { type?: unknown }).type !== "string") throw new Error("AI 服务返回了无效事件");
    onEvent(event as ChatStreamEvent);
  };
  return {
    feed(chunk: string) {
      buffer += chunk;
      for (;;) {
        const boundary = /\r\n\r\n|\n\n|\r\r/.exec(buffer);
        if (!boundary) break;
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        emit(frame);
      }
    },
    finish() { if (buffer.trim()) emit(buffer); buffer = ""; },
  };
}

export async function readEventStream(response: Response, onEvent: (event: ChatStreamEvent) => void, signal?: AbortSignal) {
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail || error));
  }
  if (!response.body) throw new Error("AI 服务没有返回流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = createEventParser(onEvent);
  let completed = false;
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener("abort", abort, { once: true });
  try {
    for (;;) {
      if (signal?.aborted) throw new DOMException("请求已停止", "AbortError");
      const { done, value } = await reader.read();
      if (signal?.aborted) throw new DOMException("请求已停止", "AbortError");
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.finish();
    completed = true;
  } finally {
    signal?.removeEventListener("abort", abort);
    if (!completed) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function contextIsCurrent(saved: ChatContext | undefined, current: ChatContext | undefined): boolean {
  if (!saved || !current || saved.project_id !== current.project_id) return false;
  if (saved.snapshot_id !== current.snapshot_id) return false;
  if (saved.revision && current.revision) return saved.revision === current.revision;
  return true;
}

export function mergeCards(existing: import("./types").ChatCard[], incoming: import("./types").ChatCard[]) {
  const cards = new Map(existing.map(card => [card.id, card]));
  for (const card of incoming) cards.set(card.id, card);
  return [...cards.values()];
}
