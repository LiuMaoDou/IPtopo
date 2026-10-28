import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import MarkdownMessage from "../src/chat/MarkdownMessage.ts";

const render = content => renderToStaticMarkup(createElement(MarkdownMessage, { content }));
test("Chinese AI replies render emphasis and lists instead of Markdown delimiters", () => {
  const html = render("根据项目已导入的配置快照：\n\n- **华为（Huawei）：9 台**\n- **瞻博网络（Juniper）：8 台**\n\n合计 **17 台**。");
  assert.match(html, /<ul>\s*<li><strong>华为（Huawei）：9 台<\/strong><\/li>/);
  assert.match(html, /<strong>瞻博网络（Juniper）：8 台<\/strong>/);
  assert.match(html, /合计 <strong>17 台<\/strong>/);
  assert.doesNotMatch(html, /\*\*/);
});
test("tables, nested lists and configuration code keep their structure", () => {
  const html = render("| 接口 | Cost |\n| --- | --- |\n| GE0/1 | 40 |\n\n1. 设备\n   - `GE0/1`\n\n```text\ninterface GE0/1\n  isis cost 40\n``` ");
  assert.match(html, /class="chat-markdown-table"/);
  assert.match(html, /<th>接口<\/th>/);
  assert.match(html, /<td>40<\/td>/);
  assert.match(html, /<ol>/);
  assert.match(html, /<ul>/);
  assert.match(html, /<pre><code class="language-text">interface GE0\/1\n  isis cost 40\n<\/code><\/pre>/);
});
test("untrusted HTML and unsafe links cannot become executable markup or automatic image loads", () => {
  const html = render('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n\n[文档](https://example.com/docs)\n\n![图](https://example.com/image.png)');
  assert.doesNotMatch(html, /<script|<img|onerror|href="javascript:/);
  assert.match(html, /href="https:\/\/example.com\/docs" target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /href="https:\/\/example.com\/image.png"/);
});
test("incomplete streamed Markdown remains renderable and resolves when complete", () => {
  const reply = "- **华为：9 台**\n\n```text\nisis cost 40\n```";
  for (let length = 1; length <= reply.length; length++) assert.doesNotThrow(() => render(reply.slice(0, length)));
  assert.match(render(reply), /<strong>华为：9 台<\/strong>/);
});
