const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../app/web/app.js"), "utf8");

function render(startedAt) {
  const elements = {};
  class Clock extends Date { static now() { return Date.parse("2026-11-03T04:00:00Z"); } }
  const context = {
    Date: Clock, labels: {}, activeSession: false,
    $: id => elements[id] ||= { disabled: false, value: "", textContent: "" },
    syncStartButtons: () => {}, sessionEstimate: () => {}, table: () => {}, time: v => v,
  };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("function renderOvernight("), source.indexOf("function renderRunProgress(")), context);
  context.renderOvernight({
    status: "running", started_at: startedAt, ends_at: "2026-11-03T07:30:00Z",
    rounds: [], planned_rounds: 5,
    config: { duration_hours: 3, interval_minutes: 45, generate_report: true, report_scope: "session" },
  });
  return { elements, context };
}

test("future UTC database times show scheduled, including legacy timezone-less strings", () => {
  for (const value of ["2026-11-03T04:30:00Z", "2026-11-03T04:30:00"]) {
    const { elements, context } = render(value);
    assert.ok(elements["overnight-status"].textContent.includes("已预约，等待启动"));
    assert.ok(elements["overnight-status"].textContent.includes("0/5"));
    assert.equal(elements["session-duration"].value, 3);
    assert.equal(elements["session-interval"].value, 45);
    assert.ok(elements["session-duration"].disabled && context.activeSession);
  }
});

test("a started session no longer claims to be waiting", () => {
  const { elements } = render("2026-11-03T03:30:00Z");
  assert.ok(!elements["overnight-status"].textContent.includes("等待启动"));
  assert.ok(elements["overnight-status"].textContent.includes("最后一轮计划"));
});
