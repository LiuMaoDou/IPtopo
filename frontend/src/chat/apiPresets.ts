import type { AISettings } from "./types";

export type ApiPreset = "openai" | "deepseek" | "custom";
export function detectApiPreset(url: string): ApiPreset {
  try {
    const host = new URL(url).hostname;
    return host === "api.deepseek.com" ? "deepseek" : host === "api.openai.com" ? "openai" : "custom";
  } catch { return "custom"; }
}
export function applyApiPreset(settings: AISettings, preset: ApiPreset): AISettings {
  if (preset === "custom") return settings;
  return { ...settings, backend: "openai", api_url: preset === "deepseek" ? "https://api.deepseek.com" : "https://api.openai.com/v1",
    api_protocol: preset === "deepseek" ? "chat_completions" : "responses",
    api_key_env: preset === "deepseek" ? "DEEPSEEK_API_KEY" : "OPENAI_API_KEY",
    model: preset === "deepseek" ? "deepseek-flash" : "" };
}
