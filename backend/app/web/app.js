"use strict";
const $ = (id) => document.getElementById(id);
const labels = {
  draft: "待确认",
  approved: "已确认",
  sent: "已投递",
  blocked: "检查未通过",
  pending: "待处理",
  running: "运行中",
  queued: "排队中",
  succeeded: "完成",
  partial: "部分可用",
  failed: "失败",
  skipped: "未采集",
  unknown: "待核对",
  sending: "投递中",
  ready: "等待定时投递",
  awaiting_email: "等待邮箱配置",
  needs_review: "需要人工核对",
  stale: "已过期",
};
const names = {
  zhihu_hot_list: "知乎热榜",
  zhihu_search: "知乎搜索",
  weibo_rsshub_hot_search: "微博热榜 RSS",
  weibo_cli: "微博 CLI",
  chinanews_scroll_rss: "中新网 RSS",
  people_politics_rss: "人民网 RSS",
  xinhua_politics_rss: "新华网 RSS",
  official_search_people: "人民网搜索",
  official_search_chinanews: "中新网搜索",
  zhihu_quota: "知乎额度查询",
};
let dashboard = {};
let previewedReportId = null;
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (cls) el.className = cls;
  return el;
}
function button(text, action, cls = "") {
  const el = node("button", text, cls);
  el.addEventListener("click", () => busy(el, action));
  return el;
}
function link(text, href) {
  const el = node("a", text);
  el.href = href;
  el.target = "_blank";
  el.rel = "noopener noreferrer";
  return el;
}
function show(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = "notice" + (error ? " error" : "");
  $("notice").hidden = false;
}
async function busy(el, action) {
  el.disabled = true;
  try {
    await action();
  } catch (error) {
    show(error.message, true);
  } finally {
    el.disabled = false;
  }
}
async function api(path, data, method) {
  const response = await fetch(path, {
    method: method || (data === undefined ? "GET" : "POST"),
    credentials: "same-origin",
    headers: data === undefined ? {} : { "Content-Type": "application/json" },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 || response.status === 503) {
      $("login").hidden = false;
      $("workspace").hidden = true;
    }
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((error) => error.msg.replace(/^Value error, /, "")).join("；")
          : JSON.stringify(body.detail),
    );
  }
  return body;
}
function time(raw) {
  if (!raw) return "未知";
  const value = /Z$|[+-]\d\d:\d\d$/.test(raw) ? raw : raw + "Z";
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
function badge(status) {
  return node("span", labels[status] || status, "status " + status);
}
function empty(container, text) {
  container.replaceChildren(node("div", text, "empty"));
}
function table(container, headers, rows) {
  const t = node("table"),
    thead = node("thead"),
    tr = node("tr"),
    body = node("tbody");
  headers.forEach((h) => tr.append(node("th", h)));
  thead.append(tr);
  rows.forEach((row) => {
    const r = node("tr");
    row.forEach((value) => {
      const c = node("td");
      c.append(
        value instanceof Node
          ? value
          : document.createTextNode(String(value ?? "未知")),
      );
      r.append(c);
    });
    body.append(r);
  });
  t.append(thead, body);
  container.replaceChildren(t);
  if (!rows.length) empty(container, "暂无记录");
}
function listSources(sources) {
  const root = $("source-list");
  root.replaceChildren();
  sources.forEach((s) => {
    const card = node("div", null, "source-card");
    card.append(
      node("h3", names[s.source_id] || s.source_id),
      badge(s.status),
      node("p", `${s.count} 条 · 更新 ${time(s.observed_at)}`, "small muted"),
    );
    if (s.error) card.append(node("p", s.error, "small muted"));
    if (s.quality_flags?.length)
      card.append(node("p", s.quality_flags.join(" / "), "small muted"));
    root.append(card);
  });
  if (!sources.length) empty(root, "尚无采集记录。点击“采集新一期”开始。");
}
function listReports(reports) {
  const root = $("report-list");
  root.replaceChildren();
  reports.forEach((r) => {
    const card = node("article", null, "report-card");
    card.append(
      node("p", `VERSION ${r.id} · 截止 ${time(r.window_end)}`, "eyebrow"),
      node("h3", r.title),
      badge(r.status),
      node(
        "p",
        `${r.event_count} 个事件${r.supersedes_id ? ` · 更正版本 #${r.supersedes_id}` : ""}`,
        "small muted",
      ),
    );
    const actions = node("div", null, "actions");
    actions.append(button("预览与确认 →", () => preview(r.id)));
    card.append(actions);
    root.append(card);
  });
  if (!reports.length) empty(root, "第一份简报将在采集完成后出现。");
}
async function preview(id, selectedRecipientIds = null) {
  previewedReportId = id;
  const r = await api(`/api/reports/${id}`);
  $("preview-panel").hidden = false;
  $("preview-title").textContent = `${r.title} / #${id}`;
  $("report-preview").src = `/api/reports/${id}/html`;
  const actions = $("preview-actions");
  actions.replaceChildren(
    link("独立页面 ↗", `/api/reports/${id}/html`),
    link("邮件预览 ↗", `/api/reports/${id}/html?email=true`),
    link("纯文本 ↗", `/api/reports/${id}/text`),
  );
  if (r.status === "draft")
    actions.append(
      button(
        "确认此版本",
        async () => {
          if (!confirm(`确认发布版本 #${id}？请先阅读正文和质量提示。`)) return;
          await api(`/api/reports/${id}/approve`, {});
          show("已确认，可发送此版本。");
          await load();
          await preview(id);
        },
        "primary",
      ),
    );
  if (["approved", "sent"].includes(r.status)) {
    const recipients = (await api("/api/recipients")).items.filter((row) => row.enabled);
    const picker = node("fieldset", null, "recipient-picker");
    picker.append(node("legend", "选择本次收件人"));
    const choices = recipients.map((row) => {
      const label = node("label", null, "check-label");
      const check = node("input");
      check.type = "checkbox";
      check.value = row.id;
      check.checked = selectedRecipientIds === null || selectedRecipientIds.includes(row.id);
      label.append(check, node("span", row.note ? `${row.email}（${row.note}）` : row.email));
      picker.append(label);
      return check;
    });
    if (!recipients.length) picker.append(node("p", "请先在“邮件管理”中添加并启用收件邮箱。", "muted"));
    actions.append(picker);
    const send = button(
      "发送给选中邮箱",
      async () => {
        const selected = choices.filter((el) => el.checked).map((el) => Number(el.value));
        if (!selected.length) return;
        if (
          !confirm(
            `将版本 #${id} 发送给选中的 ${selected.length} 位收件人？同版本已成功投递的地址会自动跳过。`,
          )
        )
          return;
        const rows = await api(`/api/reports/${id}/send`, { recipient_ids: selected });
        show(
          rows.length ? rows
            .map((d) => `${d.recipient}：${labels[d.status] || d.status}`)
            .join("；") : "收件人状态已变化，本次未投递，请刷新后检查。",
        );
        await load();
        await preview(id, selected);
      },
      "primary",
    );
    const updateSelection = () => {
      send.disabled = !dashboard.email.smtp_configured || !choices.some((el) => el.checked);
    };
    choices.forEach((el) => el.addEventListener("change", updateSelection));
    updateSelection();
    send.title = dashboard.email.smtp_configured ? "" : "请先配置发件服务";
    actions.append(send);
  }
  actions.append(
    button("生成更新草稿", async () => {
      const newReport = await api(`/api/reports/${id}/refresh`, {});
      show(`草稿已就绪：版本 #${newReport.id}`);
      await load();
      await preview(newReport.id);
    }),
  );
  const q = r.quality || {};
  $("quality").replaceChildren();
  [
    ...(q.errors || []),
    ...(q.warnings || []),
    ...(q.violations || []).map((v) => v.message),
  ].forEach((text) => $("quality").append(node("p", text)));
  $("preview-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}
function citationList(citations) {
  const ul = node("ul");
  (citations || []).forEach((c) => {
    const li = node("li");
    if (/^https?:\/\//i.test(c.url || ""))
      li.append(link(c.title || c.source_name || "查看来源", c.url));
    else li.textContent = c.title || "缺少来源链接";
    ul.append(li);
  });
  return ul;
}
async function listReviews(tasks) {
  const root = $("review-list");
  root.replaceChildren();
  if (!tasks.length) {
    empty(root, "暂无待处理候选。已接纳的事件会正常进入日报。");
    return;
  }
  for (const task of tasks) {
    const payload = task.payload || {},
      panel = node("article", null, "panel");
    panel.append(
      node("p", `REVIEW ${task.id} · ${time(task.created_at)}`, "eyebrow"),
      node("h2", payload.title || payload.event_signal?.title || "候选事件"),
      node("p", payload.reason || "需要人工核对是否属于同一事件", "muted"),
    );
    const comparison = node("div", null, "review-comparison");
    const incoming = node("div", null, "review-side"),
      existing = node("div", null, "review-side");
    incoming.append(
      node("p", "新采集线索", "eyebrow"),
      node("h3", payload.title || payload.event_signal?.title || "待确认线索"),
      citationList(payload.source_citations),
    );
    existing.append(
      node("p", "候选归并目标", "eyebrow"),
      node("p", "展开查看目标事件与匹配依据。"),
    );
    comparison.append(incoming, existing);
    panel.append(comparison);
    panel.append(
      button("查看对比依据", async () => {
        const context = await api(`/api/reviews/${task.id}/context`);
        existing.replaceChildren(
          node("p", "候选归并目标", "eyebrow"),
          node("h3", context.existing?.title || "没有匹配目标"),
          citationList(context.existing?.citations),
        );
        const details = node("details"),
          summary = node("summary", "匹配分数、实体与时间冲突详情");
        details.append(
          summary,
          node(
            "pre",
            JSON.stringify(
              {
                matched_by: payload.matched_by,
                confidence: payload.confidence,
                features: payload.match_features_json,
                flags: payload.guardrail_flags,
              },
              null,
              2,
            ),
          ),
        );
        existing.append(details);
      }),
    );
    if (task.status !== "pending") {
      panel.append(
        node(
          "p",
          "审核决定已保存，上次重算失败。重试会生成更新草稿。",
          "quality",
        ),
        button("重试分类与日报更新", async () => {
          const result = await api(`/api/reviews/${task.id}/refresh`, {});
          show(
            `重算完成${result.report_id ? `，更新草稿 #${result.report_id}` : ""}`,
          );
          await load();
        }),
      );
      root.append(panel);
      continue;
    }
    const actions = node("div", null, "review-actions"),
      reason = node("input");
    reason.placeholder = "处理理由（可选）";
    reason.setAttribute("aria-label", "处理理由");
    actions.append(reason);
    const choices = [
      ["merge_to_existing", "确认归并"],
      ["create_new_event", "单独建事件"],
      ["ignore", "忽略"],
      ["reject", "拒绝关联"],
    ];
    choices.forEach(([decision, label]) => {
      const b = button(label, async () => {
        if (!confirm(`${label}此候选？已发布简报将保留原版本，更新另存草稿。`))
          return;
        const result = await api(`/ops/human-review/tasks/${task.id}/decide`, {
          decision,
          decision_reason: reason.value,
          reviewer: "admin",
        });
        show(
          result.status === "failed" || result.refresh_status === "failed"
            ? result.message
            : `处理完成${result.report_id ? `，更新草稿 #${result.report_id}` : ""}`,
          result.status === "failed" || result.refresh_status === "failed",
        );
        await load();
      });
      b.disabled =
        decision === "merge_to_existing" && !payload.candidate_event_id;
      actions.append(b);
    });
    panel.append(actions);
    root.append(panel);
  }
}
function resetRecipientForm() {
  $("recipient-form").reset();
  $("recipient-id").value = "";
  $("recipient-form-title").textContent = "添加收件邮箱";
  $("recipient-save").textContent = "添加邮箱";
  $("recipient-cancel").hidden = true;
}
async function refreshEmailManagement() {
  await load();
  if (previewedReportId !== null) await preview(previewedReportId);
}
function listRecipients(result) {
  const root = $("recipient-list");
  root.replaceChildren();
  const warning = $("recipient-import-warning");
  warning.hidden = !result.legacy_import_skipped;
  warning.textContent = `旧配置中有 ${result.legacy_import_skipped} 个无效地址未导入，请核对后在此添加。`;
  result.items.forEach((row) => {
    const card = node("article", null, "recipient-card");
    card.append(node("h3", row.email), node("p", row.note || "未填写备注", "small muted"));
    card.append(node("span", row.enabled ? "推送已启用" : "已停用", `status ${row.enabled ? "succeeded" : "skipped"}`));
    const last = row.last_delivery;
    card.append(node("p", last ? `最近：简报 #${last.report_id} · ${labels[last.status] || last.status} · ${time(last.finished_at)}` : "暂无投递记录", "small muted recipient-last"));
    if (last?.error) card.append(node("p", last.error, "small muted"));
    const actions = node("div", null, "actions");
    actions.append(button("编辑", () => {
      $("recipient-id").value = row.id;
      $("recipient-email").value = row.email;
      $("recipient-note").value = row.note;
      $("recipient-enabled").checked = row.enabled;
      $("recipient-form-title").textContent = "编辑收件邮箱";
      $("recipient-save").textContent = "保存修改";
      $("recipient-cancel").hidden = false;
      $("recipient-form").scrollIntoView({ block: "center" });
      $("recipient-email").focus();
    }));
    actions.append(button(row.enabled ? "停用" : "启用", async () => {
      await api(`/api/recipients/${row.id}`, { email: row.email, note: row.note, enabled: !row.enabled }, "PUT");
      if ($("recipient-id").value === String(row.id)) resetRecipientForm();
      show(row.enabled ? "已停用，后续尚未发送的邮件会跳过此邮箱。" : "已启用，将参与之后的定时日报。");
      await refreshEmailManagement();
    }));
    actions.append(button("删除", async () => {
      if (!confirm(`删除收件邮箱 ${row.email}？历史投递记录会保留。`)) return;
      await api(`/api/recipients/${row.id}`, {}, "DELETE");
      if ($("recipient-id").value === String(row.id)) resetRecipientForm();
      show("已删除收件邮箱，历史投递记录保留。");
      await refreshEmailManagement();
    }, "quiet"));
    card.append(actions);
    root.append(card);
  });
  if (!result.items.length) empty(root, "还没有收件邮箱，可在上方添加。");
}
function listDeliveries(rows) {
  table(
    $("delivery-list"),
    ["版本 / 收件人", "状态", "尝试", "时间", "备注 / 操作"],
    rows.map((d) => {
      const last = node("div", d.error || "—");
      if (d.status === "unknown") {
        for (const received of [true, false])
          last.append(
            button(received ? "已核实收到" : "已核实未收到", async () => {
              if (!confirm("请先核对实际收件箱。是否保存此核对结果？")) return;
              await api(`/api/deliveries/${d.id}/resolve`, { received });
              await load();
            }),
          );
      }
      return [
        `#${d.report_id} / ${d.recipient}`,
        badge(d.status),
        d.attempts,
        time(d.finished_at || d.started_at),
        last,
      ];
    }),
  );
}
async function load() {
  const responses = await Promise.all([
    api("/api/dashboard"),
    api("/api/reports"),
    api("/api/usage"),
    api("/api/jobs"),
    api("/api/review-queue"),
    api("/api/deliveries"),
    api("/api/daily-jobs"),
    api("/api/recipients"),
  ]);
  const [dash, reports, usage, jobs, reviews, deliveries, dailyJobs, recipients] = responses;
  dashboard = dash;
  $("login").hidden = true;
  $("workspace").hidden = false;
  $("logout").hidden = false;
  $("review-count").textContent = reviews.total;
  listSources(dash.sources);
  listReports(reports);
  $("schedule-summary").textContent =
    dash.scheduler.error ||
    `定时采集${dash.scheduler.enabled ? `开启 · 每 ${dash.scheduler.interval_minutes} 分钟` : "关闭 · 可手动触发"}${dash.scheduler.until ? ` · 截止 ${time(dash.scheduler.until)}` : ""}`;
  $("email-summary").textContent = `${dash.email.smtp_configured ? "发件服务已配置" : "发件服务待配置"} · ${dash.email.recipient_count} 位启用收件人 · ${dash.email.scheduled ? `每日 ${dash.email.time} · ${dash.email.auto_generate ? "自动生成并发送" : "发送已确认版本"}` : "手动投递"} · ${dash.email.timezone}`;
  $("usage-notes").textContent =
    `工具调用 ${usage.tool_call_count} 次。${usage.notes.join(" ")}`;
  table(
    $("usage"),
    [
      "来源",
      "统计单位",
      "调用数",
      "每轮上限",
      "成功率",
      "平均耗时",
      "费用 / 失败",
    ],
    usage.requests.map((u) => [
      names[u.source_id] || u.source_id,
      u.unit === "http_request"
        ? "HTTP 请求"
        : u.unit === "cli_invocation"
          ? "CLI 调用"
          : "CLI 未安装",
      u.count,
      dash.request_limits[u.source_id] ?? "—",
      `${Math.round(u.success_rate * 100)}%`,
      `${u.mean_duration_ms} ms`,
      `${u.cost == null ? "费用未知" : u.cost} / ${Object.keys(u.errors).join(", ") || "—"}`,
    ]),
  );
  $("quotas").replaceChildren();
  if (!Object.keys(usage.quota).length)
    $("quotas").append(
      node("p", "账户额度 / 余额：未知（未查询或来源不支持）", "small muted"),
    );
  for (const [source, q] of Object.entries(usage.quota)) {
    const d = node("details");
    d.append(
      node(
        "summary",
        `${source} 额度 · ${q.status} · ${time(q.observed_at)} · ${q.provenance}`,
      ),
      node("pre", JSON.stringify(q.values, null, 2)),
    );
    $("quotas").append(d);
  }
  table(
    $("jobs"),
    ["轮次", "状态", "开始 / 完成", "结果"],
    jobs.map((j) => [
      j.run_id,
      badge(j.status),
      `${time(j.started_at)} / ${time(j.finished_at)}`,
      j.error || (j.report_id ? `简报 #${j.report_id}` : "—"),
    ]),
  );
  await listReviews(reviews.items);
  listDeliveries(deliveries);
  listRecipients(recipients);
  table(
    $("daily-jobs"),
    ["日期", "轮次", "状态", "结果"],
    dailyJobs.map((j) => [j.date, j.run_id, badge(j.status),
      j.error || (j.report_id ? `简报 #${j.report_id}` : "等待本轮采集完成")]),
  );
}
$("recipient-form").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const id = $("recipient-id").value;
    await api(id ? `/api/recipients/${id}` : "/api/recipients", {
      email: $("recipient-email").value,
      note: $("recipient-note").value,
      enabled: $("recipient-enabled").checked,
    }, id ? "PUT" : "POST");
    resetRecipientForm();
    show("收件邮箱已保存，即时生效。");
    await refreshEmailManagement();
  });
});
$("recipient-cancel").addEventListener("click", resetRecipientForm);
$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  busy(event.submitter, async () => {
    await api("/auth/session", { token: $("token").value });
    $("token").value = "";
    $("notice").hidden = true;
    await load();
  });
});
$("logout").addEventListener("click", () =>
  busy($("logout"), async () => {
    await api("/auth/logout", {});
    location.reload();
  }),
);
$("collect").addEventListener("click", () =>
  busy($("collect"), async () => {
    const row = await api("/api/jobs", {});
    show(
      `采集任务已${row.status === "running" ? "在运行" : "排队"}。请稍后刷新查看结果。`,
    );
    await load();
  }),
);
$("refresh").addEventListener("click", () => busy($("refresh"), load));
document.querySelectorAll("[data-tab]").forEach((b) =>
  b.addEventListener("click", () => {
    document
      .querySelectorAll(".tab-panel")
      .forEach((p) => (p.hidden = p.id !== b.dataset.tab));
    document
      .querySelectorAll("[data-tab]")
      .forEach((t) => t.classList.toggle("active", t === b));
  }),
);
function resizePreview() {
  try {
    $("report-preview").style.height =
      Math.max(
        600,
        $("report-preview").contentDocument.body.scrollHeight + 40,
      ) + "px";
  } catch {
    /* Separate preview remains available. */
  }
}
$("report-preview").addEventListener("load", resizePreview);
window.addEventListener("resize", resizePreview);
load().catch((error) => {
  if (!$("login").hidden) show(error.message);
  else show(error.message, true);
});
