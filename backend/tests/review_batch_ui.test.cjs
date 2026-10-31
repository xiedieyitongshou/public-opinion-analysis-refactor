// Exercise the production selection renderer with an isolated, minimal DOM.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

class Element {
  constructor(tag, text = "") { this.tag = tag; this.textContent = text; this.children = []; this.events = {}; this.isConnected = true; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(name, handler) { this.events[name] = handler; }
  querySelectorAll() { return descendants(this).filter(el => ["button", "input"].includes(el.tag)); }
}
function descendants(root) { return root.children.filter(c => c instanceof Element).flatMap(c => [c, ...descendants(c)]); }
function setup() {
  const elements = { "review-list": new Element("div"), "review-batch": new Element("div") };
  const context = {
    Map, Set, document: { createTextNode: text => text },
    $: id => elements[id], node: (tag, text) => new Element(tag, text),
    button: (text, action) => { const b = new Element("button", text); b.events.click = action; return b; },
    time: v => v, citationList: () => new Element("ul"), empty: (el, text) => el.append(new Element("p", text)),
    confirm: () => true, show: () => {}, api: async () => { throw new Error("unexpected API"); },
  };
  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, "../app/web/app.js"), "utf8");
  vm.runInContext(source.slice(source.indexOf("function reviewCategory("), source.indexOf("const selectedReviews =")), context);
  vm.runInContext(source.slice(source.indexOf("const selectedReviews ="), source.indexOf("function resetRecipientForm")), context);
  const tasks = [
    { id: 1, status: "pending", payload: { title: "X" }, merge_selection: { eligible: true, target_event_id: "a", target_title: "A", conflicts: [{ task_id: 2, target_title: "B" }] } },
    { id: 2, status: "pending", payload: { title: "X" }, merge_selection: { eligible: true, target_event_id: "b", target_title: "B", conflicts: [{ task_id: 1, target_title: "A" }] } },
    { id: 3, status: "pending", payload: { title: "Y" }, merge_selection: { eligible: true, target_event_id: "a", target_title: "A", conflicts: [] } },
  ];
  return { context, elements, tasks };
}
function checkbox(elements, id) { return descendants(elements["review-list"]).find(e => e["aria-label"] === `选择审核 ${id} 归并`); }
function action(elements, text) { return descendants(elements["review-batch"]).find(e => e.textContent === text); }

test("technical failures are labelled separately without claiming a merge suggestion", async () => {
  const { context, elements, tasks } = setup();
  tasks[0].payload.reason = "LLM comparison unavailable; no rule fallback: llm_invalid_response";
  await context.listReviews([tasks[0]]);
  assert.ok(descendants(elements["review-list"]).some(e => String(e.textContent).includes("技术异常待处理")));
  assert.equal(checkbox(elements, 1), undefined);
  await action(elements, "选择本页无互斥项").events.click();
  assert.ok(descendants(elements["review-batch"]).some(e => String(e.textContent).includes("已选择 0 条")));
  const evidence = context.reviewEvidence(tasks[0].payload, { existing: null });
  const text = descendants(evidence).map(e => e.textContent).join(" ");
  assert.ok(text.includes("这不代表模型建议合并"));
});

test("LLM review shows concrete clues and missing details instead of unused model scores", () => {
  const { context } = setup();
  const evidence = context.reviewEvidence({
    candidate_event_id: "A", confidence: 0.6,
    match_features_json: { reasoning_mode: "llm", llm_detail: {
      review_category: "plausible_uncertainty", comparisons: [{ judgement: {
        candidate_id: "A", decision: "insufficient_evidence", reason: "疑似同一事故",
        missing_information: ["具体日期"], same_event_clues: [{ quote: "甲车型在乙市撞车" }],
      } }],
    } },
  }, { existing: { title: "事故" } });
  const text = descendants(evidence).map(e => e.textContent).join(" ");
  assert.ok(text.includes("甲车型在乙市撞车"));
  assert.ok(text.includes("具体日期"));
  assert.ok(!text.includes("0.600") && !text.includes("reranker"));
});

test("a no-target review does not display dozens of unrelated comparisons", () => {
  const { context } = setup();
  const evidence = context.reviewEvidence({
    match_features_json: { reasoning_mode: "llm", llm_detail: {
      comparisons: Array.from({ length: 30 }, (_, i) => ({ judgement: {
        candidate_id: String(i), decision: "different_event", reason: "与该事件不同",
      } })),
    } },
  }, { existing: null });
  const text = descendants(evidence).map(e => e.textContent).join(" ");
  assert.ok(text.includes("没有具体的归并目标"));
  assert.ok(!text.includes("与该事件不同"));
});

test("selecting one target disables its exclusive alternative and clear restores it", async () => {
  const { context, elements, tasks } = setup();
  await context.listReviews(tasks);
  const first = checkbox(elements, 1), second = checkbox(elements, 2);
  first.checked = true; first.events.change();
  assert.equal(second.disabled, true);
  assert.equal(checkbox(elements, 3).disabled, false);
  assert.ok(descendants(elements["review-list"]).some(e => String(e.textContent).includes("与已选审核 #1 互斥")));
  await action(elements, "清空选择").events.click();
  assert.equal(second.disabled, false);
  assert.equal(first.checked, false);
});

test("safe select does not silently pick either side of an exclusive group", async () => {
  const { context, elements, tasks } = setup();
  await context.listReviews(tasks);
  await action(elements, "选择本页无互斥项").events.click();
  assert.equal(checkbox(elements, 1).checked, false);
  assert.equal(checkbox(elements, 2).checked, false);
  assert.equal(checkbox(elements, 3).checked, true);
});

test("exclusive selections remain effective when switching pages", async () => {
  const { context, elements, tasks } = setup();
  await context.listReviews([tasks[0]], { offset: 0, total: 51 });
  const first = checkbox(elements, 1); first.checked = true; first.events.change();
  await context.listReviews([tasks[1]], { offset: 50, total: 51 });
  assert.equal(checkbox(elements, 2).disabled, true);
  assert.ok(descendants(elements["review-batch"]).some(e => String(e.textContent).includes("已选择 1 条")));
});

test("batch submits each original target once and keeps results visible after reloading", async () => {
  const { context, elements, tasks } = setup();
  const requests = [];
  context.api = async (url, body) => {
    requests.push({ url, body });
    return { status: "succeeded", message: "批量归并完成", report_id: 10, refresh_status: "refreshed", items: body.items.map(e => ({ task_id: e.task_id, status: "succeeded" })) };
  };
  context.load = async () => context.listReviews([]);
  await context.listReviews(tasks);
  for (const id of [1, 3]) { const box = checkbox(elements, id); box.checked = true; box.events.change(); }
  await action(elements, "确认归并所选").events.click();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/ops/human-review/batch-merge");
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body.items)).map(e => [e.task_id, e.candidate_event_id]), [[1, "a"], [3, "a"]]);
  assert.equal(elements["review-batch"].hidden, false);
  assert.ok(descendants(elements["review-batch"]).some(e => String(e.textContent).includes("更新草稿 #10")));
});
