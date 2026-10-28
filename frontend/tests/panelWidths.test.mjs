import test from "node:test";
import assert from "node:assert/strict";
import { panelWidths, parsePanelPreferences, setPanelWidth, setPanelCollapsed } from "../src/panelWidths.ts";

test("saved widths survive serialization and stay independent between right panels", () => {
  let prefs = parsePanelPreferences(null);
  prefs = setPanelWidth(prefs, "left", "inspector", 310);
  prefs = setPanelWidth(prefs, "right", "inspector", 410);
  prefs = setPanelWidth(prefs, "right", "chat", 590);
  const saved = parsePanelPreferences(JSON.stringify(prefs));
  assert.equal(panelWidths(1600, "chat", saved).left, 310);
  assert.equal(panelWidths(1600, "chat", saved).right, 590);
  assert.equal(panelWidths(1600, "inspector", saved).right, 410);
  assert.equal(panelWidths(1600, "simulation", saved).right, 380);
});

test("narrower windows protect the topology without overwriting preferred widths", () => {
  const prefs = { version: 1, left: 480, right: { chat: 720 } };
  const before = structuredClone(prefs);
  for (const width of [1280, 1366, 1440, 1600, 1920]) {
    const size = panelWidths(width, "chat", prefs);
    assert.ok(width - size.left - size.right >= 360);
    assert.ok(size.left >= size.leftMin && size.left <= size.leftMax);
    assert.ok(size.right >= size.rightMin && size.right <= size.rightMax);
  }
  assert.equal(panelWidths(1920, "chat", prefs).right, 720);
  assert.deepEqual(prefs, before);
});

test("invalid storage falls back safely and oversize values are constrained", () => {
  for (const raw of [null, "broken JSON", "null", '{"version":2,"left":300}'])
    assert.deepEqual(parsePanelPreferences(raw), { version: 1, right: {} });
  const prefs = parsePanelPreferences('{"version":1,"left":-1,"right":{"chat":"500","simulation":1e999,"inspector":50000}}');
  assert.deepEqual(prefs, { version: 1, right: { inspector: 50000 } });
  const size = panelWidths(1280, "inspector", prefs);
  assert.ok(size.right <= size.rightMax);
  assert.ok(1280 - size.left - size.right >= 360);
});

test("reset affects only the chosen boundary and panel mode", () => {
  const prefs = { version: 1, left: 310, right: { inspector: 410, chat: 590 } };
  const reset = parsePanelPreferences(JSON.stringify(setPanelWidth(prefs, "right", "chat")));
  assert.equal(panelWidths(1500, "chat", reset).right, 390);
  assert.equal(panelWidths(1500, "inspector", reset).right, 410);
  assert.equal(reset.left, 310);
  assert.equal(prefs.right.chat, 590);
});

test("collapsing frees all sidebar space and expanding restores each remembered width", () => {
  const prefs = { version: 1, left: 310, right: { inspector: 410, chat: 590 } };
  let collapsed = setPanelCollapsed(setPanelCollapsed(prefs, "left", true), "right", true);
  collapsed = parsePanelPreferences(JSON.stringify(collapsed));
  assert.equal(panelWidths(1500, "inspector", collapsed).left, 0);
  assert.equal(panelWidths(1500, "chat", collapsed).right, 0);
  const leftOnly = setPanelCollapsed(collapsed, "left", false);
  assert.equal(panelWidths(1500, "chat", leftOnly).left, 310);
  assert.equal(panelWidths(1500, "chat", leftOnly).right, 0);
  const restored = setPanelCollapsed(leftOnly, "right", false);
  assert.equal(panelWidths(1500, "inspector", restored).right, 410);
  assert.equal(panelWidths(1500, "chat", restored).right, 590);
  assert.deepEqual(prefs, { version: 1, left: 310, right: { inspector: 410, chat: 590 } });
});

test("collapsed preferences validate booleans without accepting truthy strings", () => {
  const prefs = parsePanelPreferences('{"version":1,"collapsed":{"left":"false","right":true}}');
  assert.deepEqual(prefs.collapsed, { right: true });
  assert.ok(panelWidths(1280, "inspector", prefs).left > 0);
  assert.equal(panelWidths(1280, "inspector", prefs).right, 0);
});
