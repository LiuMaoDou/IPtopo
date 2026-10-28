import type { Ref, Selection, GraphData } from "../types";
import type { SimulationHighlight } from "../routingTypes";

export type AgentBackend = "codex" | "opencode" | "openai";
export const agentLabels: Record<AgentBackend, string> = { codex: "Codex", opencode: "OpenCode", openai: "OpenAI API" };
export type AISettings = {
  backend: AgentBackend;
  model: string;
  codex_path: string;
  opencode_url: string;
  opencode_api: "v1" | "v2";
  api_url: string;
  api_key_env: string;
  api_protocol: "auto" | "responses" | "chat_completions";
  opencode_password_env: string;
  timeout_seconds: number;
};
export const defaultAISettings: AISettings = {
  backend: "codex", model: "", codex_path: "codex", opencode_url: "http://127.0.0.1:4096", opencode_api: "v1",
  api_url: "https://api.openai.com/v1", api_key_env: "OPENAI_API_KEY", api_protocol: "auto", opencode_password_env: "OPENCODE_SERVER_PASSWORD", timeout_seconds: 120,
};
export type AgentStatus = { id?: AgentBackend; backend?: AgentBackend; label?: string; available: boolean; detail: string; version?: string };
export type ChatContext = { project_id: string; snapshot_id: string | null; revision?: string; project_version?: number; lsdb_batch_id?: string | null; [key: string]: unknown };
export type ChatEvidence = Ref & { filename?: string; label?: string };
export type ChatCard = {
  id: string;
  kind: "summary" | "devices" | "device" | "topology" | "path" | "diagnostics";
  title: string;
  data: Record<string, unknown>;
  evidence: ChatEvidence[];
  device_ids: string[];
  link_ids: string[];
  highlight?: SimulationHighlight;
};
export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; status: "running" | "completed" | "cancelled" | "error"; context: ChatContext; cards: ChatCard[]; created_at: string; error?: string };
export type ChatSession = { id: string; project_id: string; title: string; backend: AgentBackend; model: string; created_at: string; updated_at: string };
export type ChatStreamEvent =
  | { type: "start"; run_id: string; message: ChatMessage; context: ChatContext }
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; status: string; detail?: string }
  | { type: "cards"; cards: ChatCard[] }
  | { type: "done"; message: ChatMessage }
  | { type: "error"; message: string };
export type CardAction = { card: ChatCard; context: ChatContext; action: "locate" | "highlight" };
export type ChatPanelProps = {
  project: import("../types").Project;
  selection: Selection;
  canvasMode: "config" | "lsdb";
  lsdbBatchId?: string;
  revision: string;
  importing?: boolean;
  onSource: (ref: Ref) => void;
  onAction: (action: CardAction) => void;
  onClearHighlight: () => void;
  onClose: () => void;
};
export function cardTopology(card: ChatCard): GraphData | null {
  const value = card.data.topology;
  if (value && typeof value === "object" && Array.isArray((value as GraphData).nodes) && Array.isArray((value as GraphData).edges)) return value as GraphData;
  return null;
}
