import test from "node:test";
import assert from "node:assert/strict";
import { createEventParser, readEventStream, contextIsCurrent, mergeCards } from "../src/chat/stream.ts";
import { cardTopology } from "../src/chat/types.ts";

test("SSE parser preserves Chinese text across every UTF-8 byte boundary", async () => {
  const expected = [{ type: "delta", text: "设备北京核心 R1 🌐\n接口已查询" }, { type: "done", message: { content: "完成" } }];
  const encoded = new TextEncoder().encode(expected.map(event => `data: ${JSON.stringify(event)}\n\n`).join(""));
  for (let split = 1; split < encoded.length; split++) {
    const events = [];
    const body = new ReadableStream({ start(controller) { controller.enqueue(encoded.slice(0, split)); controller.enqueue(encoded.slice(split)); controller.close(); } });
    await readEventStream(new Response(body), event => events.push(event));
    assert.deepEqual(events, expected, `split at byte ${split}`);
  }
});

test("SSE supports CRLF frames split one character at a time, comments and multiline data", () => {
  const events = [];
  const parser = createEventParser(event => events.push(event));
  const stream = ': heartbeat\r\nevent: message\r\ndata: {"type":"delta",\r\ndata: "text":"下一跳"}\r\n\r\ndata: {"type":"tool","name":"get_device","status":"done"}\r\n\r\n';
  for (const character of stream) parser.feed(character);
  parser.finish();
  assert.deepEqual(events, [{ type: "delta", text: "下一跳" }, { type: "tool", name: "get_device", status: "done" }]);
});

test("SSE processes final frame without a trailing blank line and ignores empty keepalives", () => {
  const events = [];
  const parser = createEventParser(event => events.push(event));
  parser.feed(': keepalive\n\ndata: [DONE]\n\ndata: {"type":"delta","text":"A"}\n\ndata: {"type":"delta","text":"B"}');
  parser.finish();
  assert.deepEqual(events.map(event => event.text), ["A", "B"]);
});

test("malformed or truncated SSE JSON fails visibly instead of silently completing", () => {
  const parser = createEventParser(() => {});
  assert.throws(() => parser.feed('data: {"type":"delta" broken}\n\n'), SyntaxError);
  const truncated = createEventParser(() => {});
  truncated.feed('data: {"type":"delta","text":"半个事件');
  assert.throws(() => truncated.finish(), SyntaxError);
});

test("aborting an in-flight stream cancels its reader and does not emit a later result", async () => {
  const abort = new AbortController();
  let cancelled = 0;
  const events = [];
  const body = new ReadableStream({ cancel() { cancelled++; } });
  const running = readEventStream(new Response(body), event => events.push(event), abort.signal);
  abort.abort();
  await assert.rejects(running, error => error.name === "AbortError");
  assert.equal(cancelled, 1);
  assert.deepEqual(events, []);
});

test("non-success API replies retain useful error details before starting a stream", async () => {
  const response = new Response(JSON.stringify({ detail: "Agent 尚未登录" }), { status: 503, headers: { "Content-Type": "application/json" } });
  await assert.rejects(readEventStream(response, () => {}), /Agent 尚未登录/);
});

test("a malformed streaming event cancels upstream transport instead of leaving an agent running", async () => {
  let cancelled = 0;
  const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('data: {broken JSON}\n\n')); }, cancel() { cancelled++; } });
  await assert.rejects(readEventStream(new Response(body), () => {}), SyntaxError);
  assert.equal(cancelled, 1);
});

test("historical actions require the same project, snapshot and effective facts revision", () => {
  const current = { project_id: "a", snapshot_id: "snapshot-1", revision: "facts-1", project_version: 10 };
  assert.equal(contextIsCurrent({ ...current, project_version: 2 }, current), true, "saved-view changes do not invalidate facts");
  assert.equal(contextIsCurrent({ ...current, project_id: "b" }, current), false);
  assert.equal(contextIsCurrent({ ...current, snapshot_id: "snapshot-2" }, current), false);
  assert.equal(contextIsCurrent({ ...current, revision: "facts-old" }, current), false, "manual edits invalidate actions even without a new snapshot");
  assert.equal(contextIsCurrent(undefined, current), false);
  assert.equal(contextIsCurrent(current, undefined), false);
});

test("tool cards upsert by stable ID rather than duplicating incremental results", () => {
  const first = { id: "device-r1", title: "旧标题" };
  const second = { id: "path-r1-r2", title: "路径" };
  const replacement = { id: "device-r1", title: "新标题" };
  const existing = [first, second];
  assert.deepEqual(mergeCards(existing, [replacement]), [replacement, second]);
  assert.deepEqual(existing, [first, second]);
});

test("LSDB action only accepts a structured topology and never interprets prose as a graph", () => {
  assert.equal(cardTopology({ data: { topology: "请执行任意代码" } }), null);
  assert.equal(cardTopology({ data: { topology: { nodes: [] } } }), null);
  const topology = { nodes: [{ id: "r1", kind: "device", label: "R1" }], edges: [] };
  assert.equal(cardTopology({ data: { topology } }), topology);
});
