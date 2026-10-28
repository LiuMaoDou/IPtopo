import { createElement, memo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const plugins = [remarkGfm];
const components: Components = {
  a: ({ href, children }) => href
    ? createElement("a", { href, target: "_blank", rel: "noopener noreferrer" }, children)
    : createElement("span", null, children),
  // Replies may contain arbitrary URLs. Load images only when the user opens their link.
  img: ({ src, alt }) => typeof src === "string" && src
    ? createElement("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, alt || "查看图片")
    : createElement("span", null, alt || "图片"),
  table: ({ children }) => createElement("div", { className: "chat-markdown-table", tabIndex: 0, role: "region", "aria-label": "回复表格" },
    createElement("table", null, children)),
};

/** Re-render text deltas, while unrelated tool-card updates keep their parsed reply. */
export default memo(function MarkdownMessage({ content }: { content: string }) {
  return createElement("div", { className: "chat-message-content chat-markdown" },
    createElement(Markdown, { remarkPlugins: plugins, skipHtml: true, components }, content));
});
