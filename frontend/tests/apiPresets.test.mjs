import test from "node:test";
import assert from "node:assert/strict";
import { applyApiPreset, detectApiPreset } from "../src/chat/apiPresets.ts";

test("DeepSeek preset supplies its endpoint, model, protocol and own credential reference", () => {
  const original = { backend: "codex", model: "old", timeout_seconds: 120, codex_path: "/local/codex" };
  const preset = applyApiPreset(original, "deepseek");
  assert.equal(preset.api_url, "https://api.deepseek.com");
  assert.equal(preset.api_protocol, "chat_completions");
  assert.equal(preset.model, "deepseek-flash");
  assert.equal(preset.api_key_env, "DEEPSEEK_API_KEY");
  assert.equal(preset.codex_path, original.codex_path);
  assert.equal(original.backend, "codex");
});
test("custom services retain their settings and official hosts are matched exactly", () => {
  const custom = { api_url: "http://localhost:8000/v1", model: "my-model", api_key_env: "MY_MODEL_KEY" };
  assert.deepEqual(applyApiPreset(custom, "custom"), custom);
  assert.equal(detectApiPreset("https://api.deepseek.com/v1"), "deepseek");
  assert.equal(detectApiPreset("https://api.openai.com/v1"), "openai");
  assert.equal(detectApiPreset("https://api.deepseek.com.example/v1"), "custom");
  assert.equal(detectApiPreset("invalid"), "custom");
});
