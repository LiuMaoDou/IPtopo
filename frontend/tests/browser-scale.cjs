// Real-browser scale smoke test. Requires Playwright and Chrome.
// Usage: node tests/browser-scale.cjs projects.json output-directory
// projects.json: [{"id":"project id","n":100}, ...] with imported scale fixtures.
// View filters and positions are changed; use disposable QA projects.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
if (!process.argv[2])
  throw Error("Provide projects.json with disposable imported scale projects");
const projects = JSON.parse(fs.readFileSync(process.argv[2]));
const base = process.env.IPTOPO_URL || "http://127.0.0.1:8765";
const out = path.resolve(process.argv[3] || "browser-results") + "/";
fs.mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const results = [];
  for (const item of projects) {
    const live = await (await fetch(base + "/api/projects/" + item.id)).json();
    if (live.views[0])
      await fetch(
        base +
          "/api/projects/" +
          item.id +
          "/views/" +
          live.views[0].id +
          "?summary=true",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            version: live.version,
            view: {
              ...live.views[0],
              filters: {
                search: "",
                family: "all",
                vendor: "all",
                domain: "all",
                mode: "device",
                focus: "",
              },
            },
          }),
        },
      );
    const page = await browser.newPage({
      viewport: { width: 1536, height: 1024 },
      acceptDownloads: true,
    });
    const errors = [],
      puts = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("response", async (r) => {
      if (r.request().method() === "PUT" && r.url().includes("/views/"))
        puts.push({
          url: r.url(),
          bytes: (await r.body()).length,
          status: r.status(),
        });
    });
    await page.addInitScript((id) => {
      localStorage.setItem("iptopo.project", id);
      window.longtasks = [];
      new PerformanceObserver((l) =>
        window.longtasks.push(...l.getEntries().map((e) => e.duration)),
      ).observe({ type: "longtask", buffered: true });
    }, item.id);
    const start = Date.now();
    await page.goto(base);
    await page.waitForFunction(
      (n) => document.querySelector(".graph")?._cyreg?.cy?.nodes().length === n,
      item.n,
      { timeout: 30000 },
    );
    await page.evaluate(
      () =>
        new Promise((r) =>
          requestAnimationFrame(() => requestAnimationFrame(r)),
        ),
    );
    const ready = Date.now() - start;
    await page.waitForTimeout(1200);
    const name = await page.locator(".device-row strong").first().innerText();
    const t = Date.now();
    await page.getByLabel("搜索设备、接口或地址").fill(name);
    await page.waitForFunction(
      () => document.querySelector(".graph")._cyreg.cy.nodes().length === 1,
    );
    const searchMs = Date.now() - t;
    await page.getByLabel("搜索设备、接口或地址").fill("");
    await page.waitForFunction(
      (n) => document.querySelector(".graph")._cyreg.cy.nodes().length === n,
      item.n,
    );
    const tabs = {};
    for (const [label, selector] of [
      ["文件", ".file-row"],
      ["地址域", ".context-row"],
      ["设备", ".device-row"],
    ]) {
      const t = Date.now();
      await page
        .locator(".inventory .tabs")
        .getByRole("button", { name: label, exact: label !== "设备" })
        .click();
      await page.waitForTimeout(50);
      tabs[label] = {
        ms: Date.now() - t,
        rows: await page.locator(".inventory " + selector).count(),
      };
    }
    const before = await page
      .locator(".graph")
      .evaluate((el) =>
        Object.fromEntries(
          el._cyreg.cy.nodes().map((n) => [n.id(), n.position()]),
        ),
      );
    await page.locator(".device-row").first().click();
    await page.getByRole("button", { name: "聚焦相邻", exact: true }).click();
    await page.waitForTimeout(300);
    const focusNodes = await page
      .locator(".graph")
      .evaluate((el) => el._cyreg.cy.nodes().length);
    if (item.n === 1000)
      await page.screenshot({ path: out + "iptopo-scale-1000-focus.png" });
    await page.getByRole("button", { name: "显示全部", exact: true }).click();
    await page.waitForTimeout(300);
    const after = await page
      .locator(".graph")
      .evaluate((el) =>
        Object.fromEntries(
          el._cyreg.cy.nodes().map((n) => [n.id(), n.position()]),
        ),
      );
    let preserved = Object.keys(before).every(
      (id) => JSON.stringify(before[id]) === JSON.stringify(after[id]),
    );
    const rr = {
      n: item.n,
      ready_ms: ready,
      search_ms: searchMs,
      inventory_rows: await page.locator(".device-row").count(),
      tabs,
      focus_nodes: focusNodes,
      focus_reset_positions: preserved,
    };
    if (item.n === 1000) {
      await page.getByRole("button", { name: "关闭详情", exact: true }).click();
      await page.screenshot({ path: out + "iptopo-scale-1000-overview.png" });
      await page.getByLabel("布局算法").selectOption("grid");
      await page.getByTitle("运行自动布局").click();
      await page.waitForTimeout(400);
      await page.getByLabel("搜索设备、接口或地址").fill(name);
      await page.waitForTimeout(200);
      await page.getByLabel("撤销布局", { exact: true }).click();
      await page.getByLabel("搜索设备、接口或地址").fill("");
      await page.waitForTimeout(400);
      const undo = await page
        .locator(".graph")
        .evaluate((el) =>
          Object.fromEntries(
            el._cyreg.cy.nodes().map((n) => [n.id(), n.position()]),
          ),
        );
      rr.hidden_undo_positions = Object.keys(before).every(
        (id) => JSON.stringify(before[id]) === JSON.stringify(undo[id]),
      );
    }
    if (item.n === 2000) {
      await page.getByLabel("布局算法").selectOption("elk");
      await page.getByTitle("运行自动布局").click();
      await page.getByLabel("搜索设备、接口或地址").fill(name);
      await page.waitForTimeout(100);
      rr.worker_cancelled_by_filter =
        (await page.locator(".layout-progress").count()) === 0;
      await page.getByLabel("搜索设备、接口或地址").fill("");
      await page.waitForTimeout(300);
      await page.locator(".graph").evaluate((el) => {
        window.graphDataEvents = 0;
        el._cyreg.cy.on("data", () => window.graphDataEvents++);
      });
      await page.getByLabel("放大", { exact: true }).click();
      await page.waitForTimeout(2200);
      rr.data_events_on_view_save = await page.evaluate(
        () => window.graphDataEvents,
      );
      await page
        .locator(".inventory .tabs")
        .getByRole("button", { name: "地址域", exact: true })
        .click();
      await page
        .getByRole("button", { name: "管理地址域", exact: true })
        .click();
      rr.domain_dialog_rows = await page.locator("dialog tbody tr").count();
      await page.getByLabel("关闭弹窗", { exact: true }).click();
      await page
        .locator(".inventory .tabs")
        .getByRole("button", { name: "设备", exact: false })
        .click();
      await page.getByLabel("适应画布", { exact: true }).click();
      await page.waitForTimeout(300);
      await page.screenshot({ path: out + "iptopo-scale-2000-overview.png" });
      await page.getByRole("button", { name: "导出", exact: true }).click();
      const pending = page.waitForEvent("download", { timeout: 30000 });
      await page.getByRole("button", { name: "拓扑 PNG", exact: true }).click();
      const download = await pending;
      const dest = out + "iptopo-scale-2000.png";
      await download.saveAs(dest);
      const png = fs.readFileSync(dest);
      rr.png = {
        bytes: png.length,
        width: png.readUInt32BE(16),
        height: png.readUInt32BE(20),
        signature: png.subarray(1, 4).toString(),
      };
    }
    rr.long_tasks = await page.evaluate(() => ({
      count: window.longtasks.length,
      max_ms: Math.max(0, ...window.longtasks),
    }));
    rr.puts = puts;
    rr.errors = errors;
    results.push(rr);
    console.log(JSON.stringify(rr));
    fs.writeFileSync(
      out + "browser-results.json",
      JSON.stringify(results, null, 2),
    );
    await page.close();
  }
  if (process.env.IPTOPO_DEMO_PROJECT) {
    const p = await browser.newPage({
      viewport: { width: 1536, height: 1024 },
    });
    await p.addInitScript(
      (id) => localStorage.setItem("iptopo.project", id),
      process.env.IPTOPO_DEMO_PROJECT,
    );
    await p.goto(base);
    await p.locator(".device-row").first().waitFor();
    await p.waitForTimeout(1200);
    await p.getByLabel("适应画布", { exact: true }).click();
    await p.waitForTimeout(300);
    await p.screenshot({ path: out + "iptopo-vendor-icons.png" });
    await p.setViewportSize({ width: 1280, height: 800 });
    await p.waitForTimeout(300);
    await p.locator(".device-row").first().click();
    await p.locator(".inspector").waitFor({ state: "visible" });
    await p.screenshot({ path: out + "iptopo-scale-desktop-1280-detail.png" });
    const desktopLayout = await p.evaluate(() => ({
      viewport: "1280x800",
      overflow: document.documentElement.scrollWidth > innerWidth,
      inventory: getComputedStyle(document.querySelector(".inventory")).display !== "none",
      canvas: getComputedStyle(document.querySelector(".canvas-section")).display !== "none",
      inspector: getComputedStyle(document.querySelector(".inspector")).display !== "none",
    }));
    if (desktopLayout.overflow || !desktopLayout.inventory || !desktopLayout.canvas || !desktopLayout.inspector)
      throw Error("Desktop three-column layout failed: " + JSON.stringify(desktopLayout));
    console.log(
      "PC_1280_LAYOUT",
      JSON.stringify(desktopLayout),
    );
  }
  await browser.close();
})();
