const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag, text = "") { this.tag = tag; this.text = text; this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
function text(element) {
  return typeof element === "string" ? element : [element.text, ...element.children.map(text)].join(" ");
}
function setup(history = []) {
  const root = new Element("div");
  const context = {
    Date, Number, Set, names: { zhihu_hot_list: "知乎热榜" }, $: () => root,
    document: { createTextNode: value => value },
    node: (tag, value, cls) => Object.assign(new Element(tag, value), { cls }),
    button: (value, action) => Object.assign(new Element("button", value), { click: action }),
    badge: value => new Element("span", value), empty: () => {},
    api: async () => history, preview: () => {}, openReportReviews: () => {},
  };
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, "../app/web/app.js"), "utf8");
  vm.runInContext(source.slice(source.indexOf("function reportTime("), source.indexOf("async function preview(")), context);
  return { context, root };
}
const record = {
  id: 6, title: "旧采集的新版本", status: "draft", event_count: 10,
  window_start: "2026-10-30T22:00:00Z", window_end: "2026-10-31T01:00:00Z",
  created_at: "2026-11-03T00:00:00",
  collection_provenance: {
    first_observed_at: "2026-10-30T22:05:00Z", last_observed_at: "2026-10-31T00:15:00Z",
    round_count: 4, expected_round_count: 4, complete: true,
    sources: [{ source_id: "zhihu_hot_list" }],
  },
};

test("archive separates Beijing capture dates from new version creation", () => {
  const { context, root } = setup();
  context.listReports([record]);
  const result = text(root);
  assert.ok(result.includes("2026/10/31 06:05"));
  assert.ok(result.includes("2026/11/03 08:00"));
  assert.ok(result.includes("数据采集 · 4轮") && result.includes("知乎热榜"));
  assert.ok(result.includes("简报范围") && result.includes("版本生成"));
});

test("expanded older revisions also show original capture provenance", async () => {
  const old = { ...record, id: 5, created_at: "2026-11-02T00:00:00Z" };
  const { context, root } = setup([old]);
  context.listReports([record]);
  const toggle = root.children[0].children.find(e => e.text === "查看历史版本");
  await toggle.click();
  assert.ok(text(root).includes("版本 #5 · 查看"));
  assert.ok(text(root).includes("2026/11/02 08:00"));
  const metadata = context.reportMetadata({ id: 1 });
  assert.ok(text(metadata).includes("未记录"));
  assert.ok(!text(metadata).includes("Invalid Date"));
});
