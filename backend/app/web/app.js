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
let activeRun = false;
let activeSession = false;
function syncStartButtons() {
  for (const id of ["collect", "overnight-start"]) {
    $(id).disabled = activeRun || activeSession || $(id).dataset.busy === "true";
  }
}
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
  el.dataset.busy = "true";
  el.disabled = true;
  try {
    await action();
  } catch (error) {
    show(error.message, true);
  } finally {
    delete el.dataset.busy;
    el.disabled = false;
    if (["collect", "overnight-start"].includes(el.id)) syncStartButtons();
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
    const history = node("div");
    history.hidden = true;
    let loaded = false;
    const toggle = button("查看历史版本", async () => {
      if (!loaded) {
        const versions = await api(`/api/reports/${r.id}/history`);
        if (!versions.length) empty(history, "暂无历史版本");
        for (const version of versions) {
          const entry = node("p");
          entry.append(badge(version.status), document.createTextNode(" "),
            button(`#${version.id} · ${time(version.created_at)} · 查看`, () => preview(version.id), "quiet"));
          history.append(entry);
        }
        loaded = true;
      }
      history.hidden = !history.hidden;
      toggle.textContent = history.hidden ? "查看历史版本" : "收起历史版本";
    }, "quiet");
    card.append(toggle, history);
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
function reviewEvidence(payload, context) {
  const root = node("div", null, "review-evidence");
  if (!context.existing) {
    root.append(node("h4", "没有已接纳的归并目标"), node("p",
      (payload.reason || "").includes("discovery parent")
        ? "增强数据正在等待父话题确定归属；父话题处理后将核对并回挂。"
        : "这是独立话题的处理，并非与某个目标的合并确认。历史检索分数不代表当前匹配。"));
    return root;
  }
  const features = payload.match_features_json || {};
  const incoming = payload.event_signal || {};
  const existing = context.existing?.detail?.match_features || {};
  const missing = new Set(features.missing_features || []);
  const flags = [...new Set([...(payload.guardrail_flags || []), ...(features.guardrail_flags || [])])];
  const flagLabels = {
    low_confidence: "匹配置信度不足", weak_signal_only: "线索缺少明确的事件信息",
    entity_conflict: "实体冲突", time_conflict: "时间冲突", action_conflict: "动作冲突",
    cluster_conflict: "已有事件内部证据不一致",
    embedding_only_without_hard_constraint: "仅有语义相似，缺少身份或事实依据",
    semantic_false_positive_risk: "存在语义误匹配风险",
  };
  const score = value => typeof value === "number" ? value.toFixed(3) : "未计算";
  const terms = value => Array.isArray(value) && value.length ? value.join("、") : "未提取";
  const conflict = (name, absent) => flags.includes(`${name}_conflict`) ? "存在冲突"
    : absent ? "信息不足，无法判断" : "未检测到冲突";
  root.append(node("h4", "匹配依据"), node("p",
    flags.length ? flags.map(flag => flagLabels[flag] || `其他检查项：${flag}`).join("；")
      : "需结合两侧来源判断是否为同一事件。", "quality"));
  const scores = node("div");
  table(scores, ["指标", "数值"], [
    ["综合匹配置信度", score(payload.confidence)],
    ["重排模型分数（reranker）", score(features.rerank_score)],
    ["语义相似度（embedding）", score(features.embedding_similarity)],
    ["同一身份 ID / 同一链接", `${features.id_match ? "是" : "否"} / ${features.url_match ? "是" : "否"}`],
  ]);
  root.append(scores, node("p", "分数是匹配指标，不是事实真实性的概率；未提取信息不等于存在冲突。", "small muted"));
  const comparison = node("div");
  table(comparison, ["对比项", "新线索", "已有事件", "结果"], [
    ["实体", terms(incoming.entities), terms(existing.entities),
      conflict("entity", missing.has("entities") || !incoming.entities?.length || !existing.entities?.length)],
    ["动作", terms(incoming.action_terms), terms(existing.action_terms),
      conflict("action", missing.has("action_terms") || !incoming.action_terms?.length || !existing.action_terms?.length)],
    ["事件时间", incoming.event_time_hint ? time(incoming.event_time_hint) : "未提取",
      existing.event_time_hint ? time(existing.event_time_hint) : "未提取",
      conflict("time", missing.has("event_time") || !incoming.event_time_hint || !existing.event_time_hint)],
    ["实体 / 动作重合度", "—", "—", `${score(features.entity_overlap)} / ${score(features.action_overlap)}`],
  ]);
  root.append(comparison);
  const details = node("details");
  details.append(node("summary", "技术详情（原始数据）"), node("pre", JSON.stringify({
    matched_by: payload.matched_by, confidence: payload.confidence,
    features, flags,
  }, null, 2)));
  root.append(details);
  return root;
}

const selectedReviews = new Map();
const reviewReasons = new Map();
let reviewSubmitting = false;
let reviewPageOffset = 0;
let reviewPageTotal = 0;
async function listReviews(tasks, paging) {
  if (paging) { reviewPageOffset = paging.offset || 0; reviewPageTotal = paging.total; }
  const root = $("review-list");
  const toolbar = $("review-batch");
  root.replaceChildren();
  toolbar.replaceChildren();
  const eligible = new Map(tasks.filter(t => t.status === "pending" && t.merge_selection?.eligible)
    .map(t => [t.id, t]));
  for (const [id, target] of selectedReviews) {
    if (tasks.some(t => t.id === id) && eligible.get(id)?.merge_selection.target_event_id !== target) selectedReviews.delete(id);
  }
  if (!tasks.length) {
    selectedReviews.clear();
    toolbar.hidden = true;
    empty(root, "暂无待处理候选。已接纳的事件会正常进入日报。");
    return;
  }
  toolbar.hidden = false;
  const summary = node("p"), batchResult = node("div", null, "small");
  const checkboxes = new Map(), hints = new Map();
  const conflictsWithSelection = task => (task.merge_selection?.conflicts || [])
    .filter(c => selectedReviews.has(c.task_id));
  function updateSelection() {
    summary.textContent = `已选择 ${selectedReviews.size} 条。各条归入各自展示的目标，全部完成后统一更新热度与简报。`;
    submit.disabled = reviewSubmitting || !selectedReviews.size;
    selectSafe.disabled = clear.disabled = reviewSubmitting;
    previous.disabled = reviewSubmitting || reviewPageOffset === 0;
    next.disabled = reviewSubmitting || reviewPageOffset + tasks.length >= reviewPageTotal;
    for (const task of tasks) {
      const checkbox = checkboxes.get(task.id);
      if (!checkbox) continue;
      const conflicts = conflictsWithSelection(task);
      checkbox.checked = selectedReviews.has(task.id);
      checkbox.disabled = reviewSubmitting || !task.merge_selection?.eligible || conflicts.length > 0
        || (!checkbox.checked && selectedReviews.size >= 100);
      hints.get(task.id).textContent = conflicts.length
        ? `与已选审核 #${conflicts.map(c => c.task_id).join("、#")} 互斥，请先取消另一项。`
        : task.merge_selection?.unavailable_reason || "";
    }
  }
  const submit = button("确认归并所选", async () => {
    if (!selectedReviews.size || reviewSubmitting) return;
    const entries = [...selectedReviews].map(([task_id, candidate_event_id]) => ({
      task_id, candidate_event_id, decision_reason: reviewReasons.get(task_id) || null,
    }));
    if (!confirm(`确认将所选 ${entries.length} 条分别归入各自的候选目标？只生成一次更新草稿。`)) return;
    reviewSubmitting = true;
    root.querySelectorAll("button, input").forEach(el => { el.disabled = true; });
    updateSelection();
    batchResult.replaceChildren(node("p", "正在逐条保存归并结果并统一重算，请等待；不会重新采集或发送邮件。"));
    let reloaded = false;
    try {
      const result = await api("/ops/human-review/batch-merge", { items: entries, reviewer: "admin" });
      const details = (result.conflicts || []).map(c => `审核 #${c.task_ids.join("、#")}：${c.reason}`);
      for (const item of result.items || []) {
        details.push(`审核 #${item.task_id}：${item.status === "succeeded" ? "已归并" : item.message || item.status}`);
        if (item.status === "succeeded" || item.status === "skipped") selectedReviews.delete(item.task_id);
      }
      const message = `${result.message}${result.report_id ? `，更新草稿 #${result.report_id}` : ""}`;
      show(message, result.status !== "succeeded" || result.refresh_status === "failed");
      reviewSubmitting = false;
      await load();
      reloaded = true;
      $("review-batch").hidden = false;
      $("review-batch").append(node("p", message), ...details.map(s => node("p", s, "small")));
    } finally {
      reviewSubmitting = false;
      if (!reloaded && root.isConnected) {
        // Restore existing single-action availability after a transport failure too.
        await listReviews(tasks);
      }
    }
  });
  const selectSafe = button("选择本页无互斥项", () => {
    for (const task of eligible.values()) {
      if (selectedReviews.size < 100 && !task.merge_selection.conflicts.length) selectedReviews.set(task.id, task.merge_selection.target_event_id);
    }
    updateSelection();
  });
  const clear = button("清空选择", () => { selectedReviews.clear(); updateSelection(); });
  const previous = button("上一页", async () => { reviewPageOffset = Math.max(0, reviewPageOffset - 50); await load(); });
  const next = button("下一页", async () => { reviewPageOffset += 50; await load(); });
  toolbar.append(node("h3", "批量归并"), summary,
    node("p", "同一线索指向不同事件时只能选一个；翻页保留选择，最多100条。请逐条核对目标。", "small muted"),
    node("p", `当前第 ${reviewPageOffset + 1}–${reviewPageOffset + tasks.length} 条，共 ${reviewPageTotal || tasks.length} 条`, "small"),
    previous, next, selectSafe, clear, submit, batchResult);
  for (const task of tasks) {
    const payload = task.payload || {},
      panel = node("article", null, "panel");
    panel.append(
      node("p", `REVIEW ${task.id} · ${time(task.created_at)}`, "eyebrow"),
      node("h2", payload.title || payload.event_signal?.title || "候选事件"),
      node("p", `累计涉及 ${new Set([...(payload.run_ids || []), payload.run_id].filter(Boolean)).size} 轮采集`, "muted"),
      node("p", payload.reason || "需要人工核对是否属于同一事件", "muted"),
    );
    if (task.status === "pending") {
      const selection = task.merge_selection || {};
      const label = node("label", null, "review-select");
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.setAttribute("aria-label", `选择审核 ${task.id} 归并`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selectedReviews.set(task.id, selection.target_event_id);
        else selectedReviews.delete(task.id);
        updateSelection();
      });
      label.append(checkbox, document.createTextNode("加入批量归并"));
      const hint = node("p", null, "quality small");
      checkboxes.set(task.id, checkbox); hints.set(task.id, hint);
      panel.append(label,
        node("p", `归并目标：${selection.target_title || "没有可用目标"}`, "review-target"), hint);
      if (selection.conflicts?.length) {
        panel.append(node("p", `互斥关系：${selection.conflicts.map(c => `审核 #${c.task_id} → ${c.target_title}`).join("；")}。只能选择其中一个目标。`, "review-exclusive"));
      }
    }
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
        existing.append(reviewEvidence(payload, context));
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
    reason.value = reviewReasons.get(task.id) || "";
    reason.addEventListener("input", () => reviewReasons.set(task.id, reason.value));
    actions.append(reason);
    const eventTitle = node("input");
    eventTitle.value = payload.title || payload.event_signal?.title || "";
    eventTitle.placeholder = "独立事件名称（可修改）";
    eventTitle.setAttribute("aria-label", "独立事件名称");
    eventTitle.maxLength = 500;
    actions.append(eventTitle);
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
          ...(decision === "create_new_event" ? { event_title: eventTitle.value.trim() || null } : {}),
        });
        show(
          result.status === "failed" || result.refresh_status === "failed"
            ? result.message
            : `处理完成${result.report_id ? `，更新草稿 #${result.report_id}` : ""}`,
          result.status === "failed" || result.refresh_status === "failed",
        );
        await load();
      });
      b.disabled = reviewSubmitting || (decision === "merge_to_existing" && !task.merge_selection?.eligible);
      actions.append(b);
    });
    panel.append(actions);
    root.append(panel);
  }
  updateSelection();
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
function renderOvernight(night) {
  night = night || {status: "idle", rounds: []};
  const activeNight = ["running", "stopping"].includes(night.status);
  activeSession = activeNight;
  syncStartButtons();
  $("overnight-stop").hidden = !activeNight;
  const nightLabels = {idle:"尚未启动",running:"运行中",stopping:"等待当前轮次完成后汇总",completed:"已完成",stopped:"已停止",failed:"失败"};
  $("overnight-status").textContent = `${nightLabels[night.status] || night.status} · ${night.rounds.length}/${night.planned_rounds ?? "持续"} 轮已安排${night.ends_at && night.config?.duration_hours !== null ? ` · 计划结束 ${time(night.ends_at)}` : ""}${night.error ? ` · ${night.error}` : ""}`;
  if (night.report_id) $("overnight-status").append(" · ", link("打开测试简报", `/api/reports/${night.report_id}/html`));
  if (night.backup_file) $("overnight-status").append(` · 已备份：backups/${night.backup_file}`);
  table($("overnight-rounds"), ["轮次", "状态", "异常"], night.rounds.map(r => [r.slot + 1, labels[r.status] || r.status, r.error || "—"]));
}

function renderRunProgress(run) {
  activeRun = ["queued", "running"].includes(run.status);
  syncStartButtons();
  const stages = {
    fetch_source_items: "采集数据", normalize_raw_items: "整理采集数据",
    prepare_source_signals: "准备来源证据", extract_event_signals: "提取事件",
    match_and_resolve_events: "匹配与归并事件", create_human_review_task: "整理人工审核",
    calculate_event_scores: "计算平台内热度", classify_events: "官媒补证与事件分类",
    analyze_event_heat: "分析热度趋势", generate_daily_briefing: "生成简报",
    review_briefing_quality: "检查简报质量", save_daily_report: "保存简报", idle: "暂无任务"
  };
  const status = {...labels, waiting:"等待执行", interrupted:"已中断", not_observed:"未记录", idle:"暂无任务"};
  const sourceNames = {...names, weibo_rss:"微博 RSS 获取", weibo_rsshub_hot_search:"微博采集与增强"};
  $("run-status").textContent = run.run_id
    ? `${status[run.status] || run.status} · ${stages[run.stage] || status[run.stage] || (activeRun ? "准备执行" : "任务结束")} · ${Math.floor(run.elapsed_seconds / 60)}分${run.elapsed_seconds % 60}秒 · 开始 ${time(run.started_at)}${run.error ? ` · ${run.error}` : ""}`
    : "暂无采集任务";
  if (run.report_id) $("run-status").append(" · ", link("查看简报", `/api/reports/${run.report_id}/html`));
  $("run-refresh").textContent = `状态读取于 ${new Date().toLocaleTimeString()} · 每5秒刷新，不触发采集${run.updated_at ? ` · 最近执行进展 ${time(run.updated_at)}` : ""}`;
  table($("run-sources"), ["模块", "状态", "进度／返回数据", "异常"], run.sources.map(s => [
    sourceNames[s.source_id] || s.source_id, status[s.status] || s.status || "等待执行",
    `${s.total != null ? `${s.completed || 0}/${s.total}个话题；` : ""}${s.returned_count != null ? `返回${s.returned_count}条` : "—"}${s.accepted_count != null ? `，接纳${s.accepted_count}条` : ""}`,
    s.error || (s.failed_topics ? `${s.failed_topics}个话题增强失败` : "—")
  ]));
  table($("run-usage"), ["接口来源", "已结束请求", "成功", "失败", "累计耗时"], run.requests.map(r => [
    sourceNames[r.source_id] || r.source_id, r.count, r.count - r.failed, r.failed, `${(r.duration_ms / 1000).toFixed(1)}秒`
  ]));
  const cli = run.sources.find(s => s.source_id === "weibo_cli");
  $("run-usage-note").textContent = `${run.run_id ? `轮次 ${run.run_id}。` : ""}请求完成后更新；进行中的请求尚未计入。${cli?.returned_count != null ? `微博CLI已解析${cli.returned_count}条，按3积分/条估算${cli.returned_count * 3}积分。` : ""}估算不含无法解析的响应及启动检查，不是实际账单；额度见下方。`;
}

async function load() {
  const responses = await Promise.all([
    api("/api/dashboard"),
    api("/api/reports"),
    api("/api/usage"),
    api("/api/jobs"),
    api(`/api/review-queue?offset=${reviewPageOffset}`),
    api("/api/deliveries"),
    api("/api/daily-jobs"),
    api("/api/recipients"),
    api("/api/run-progress"),
  ]);
  const [dash, reports, usage, jobs, initialReviews, deliveries, dailyJobs, recipients, progress] = responses;
  let reviews = initialReviews;
  if (reviewPageOffset && reviewPageOffset >= reviews.total) {
    reviewPageOffset = 0;
    reviews = await api("/api/review-queue");
  }
  dashboard = dash;
  renderOvernight(dash.overnight);
  renderRunProgress(progress);
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
  await listReviews(reviews.items, reviews);
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
      `采集任务已${row.status === "running" ? "在运行" : "排队"}，主页会自动更新进度。`,
    );
    await load();
  }),
);
$("refresh").addEventListener("click", () => busy($("refresh"), load));
$("overnight-start").addEventListener("click", () => busy($("overnight-start"), async () => {
  const fresh = $("session-fresh").checked;
  if (fresh && !confirm("将先备份数据库，再清空当前采集、事件、审核和简报，开始独立采集。邮箱及收件人配置保留。确定继续？")) return;
  await api("/api/overnight-test", {
      fresh_start: fresh,
      duration_hours: $("session-duration").value ? Number($("session-duration").value) : null,
      interval_minutes: Number($("session-interval").value),
      generate_report: $("session-finish").value !== "none",
      report_scope: !fresh && $("session-finish").value === "last24h" ? "last24h" : "session"
    });
  show("采集任务已启动，按所选配置运行，不发送邮件。");
  await load();
}));
$("overnight-stop").addEventListener("click", () => busy($("overnight-stop"), async () => {
  await api("/api/overnight-test/stop", {});
  show("已请求结束，当前轮次完成后汇总。");
  await load();
}));
document.querySelectorAll("[data-tab]").forEach((b) =>
  b.addEventListener("click", () => {
    if (b.dataset.tab === "mail-settings") loadMailSettings().catch(e => show(e.message, true));
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


const mailFields = [
  ["smtp_host", "SMTP 主机", "text"], ["smtp_port", "端口", "number"],
  ["smtp_security", "加密方式", "select"], ["smtp_username", "发件账号", "text"],
  ["email_from", "发件邮箱地址", "email"],
  ["daily_briefing_enabled", "每天自动生成并确认日报", "checkbox"],
  ["email_schedule_enabled", "每天自动发送", "checkbox"],
  ["email_send_time", "每日触发时间", "time"], ["briefing_timezone", "时区", "text"],
  ["smtp_timeout_seconds", "连接超时（秒，1～60）", "number"],
  ["email_max_attempts", "最多尝试次数（1～5）", "number"],
];
for (const [key, label, type] of mailFields) {
  const wrap = node("label", label);
  const input = document.createElement(type === "select" ? "select" : "input");
  input.id = `mail-${key}`;
  if (type === "select") {
    for (const value of ["ssl", "starttls", "plain"]) {
      const option = node("option", value); option.value = value; input.append(option);
    }
  } else input.type = type;
  if (type === "checkbox") wrap.className = "check-label";
  if (type === "number") {
    input.min = "1";
    input.max = key === "smtp_port" ? "65535" : key === "email_max_attempts" ? "5" : "60";
  }
  wrap.append(input); $("mail-settings-fields").append(wrap);
}
async function loadMailSettings() {
  const config = await api("/api/email-settings");
  for (const [key, , type] of mailFields) {
    const input = $(`mail-${key}`);
    if (type === "checkbox") input.checked = config[key];
    else input.value = config[key] ?? "";
  }
  $("mail-password").value = "";
  $("mail-clear-password").checked = false;
  $("mail-settings-source").textContent = `配置来源：${config.source === "database" ? "管理页面（数据库）" : "部署环境"} · 授权码${config.password_set ? "已设置" : "未设置"} · ${config.encryption_ready ? "加密密钥已配置" : "保存授权码前需配置 EMAIL_SETTINGS_KEY"}`;
}
$("mail-settings-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const data = {};
    for (const [key, , type] of mailFields) {
      const input = $(`mail-${key}`);
      data[key] = type === "checkbox" ? input.checked : type === "number" ? Number(input.value) : input.value;
    }
    if ($("mail-password").value) data.smtp_password = $("mail-password").value;
    data.clear_password = $("mail-clear-password").checked;
    await api("/api/email-settings", data, "PUT");
    await loadMailSettings();
    show("邮件设置已保存，后续任务使用新配置。未发送测试邮件。");
  });
});
$("mail-check").addEventListener("click", () => busy($("mail-check"), async () => {
  const result = await api("/api/email-settings/check", {});
  $("mail-test-result").textContent = `${result.status}：${result.message}`;
}));
$("mail-test-form").addEventListener("submit", event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    const recipient = $("mail-test-recipient").value;
    if (!confirm(`向 ${recipient} 发送一封真实测试邮件？使用已保存配置。`)) return;
    const result = await api("/api/email-settings/test", {recipient});
    $("mail-test-result").textContent = `${result.status}：${result.message}`;
  });
});

let progressPolling = false;
setInterval(async () => {
  if ($("workspace").hidden || document.hidden) return;
  if (progressPolling) return;
  progressPolling = true;
  try {
    const [night, run] = await Promise.all([api("/api/overnight-test"), api("/api/run-progress")]);
    renderOvernight(night); renderRunProgress(run);
  } catch (_) { $("run-refresh").textContent = "状态刷新失败，正在重试；上方是最后一次成功读取的数据。"; }
  finally { progressPolling = false; }
}, 5000);

function sessionEstimate() {
  const hours = Number($("session-duration").value), minutes = Number($("session-interval").value);
  $("session-estimate").textContent = hours && minutes ? "计划 " + (Math.floor(hours * 60 / minutes) + 1) + " 轮；每轮采集两个平台并调用增强接口，实际用量取决于话题数与重试。启动检查另调用一次微博搜索。" : "持续运行，直到手动停止；会持续消耗接口额度。";
}
["session-duration","session-interval"].forEach(id => $(id).addEventListener("input", sessionEstimate));
$("session-preset").addEventListener("click", () => {
  $("session-duration").value = 8; $("session-interval").value = 60;
  $("session-finish").value = "session"; sessionEstimate();
});
$("report-window").addEventListener("click", () => busy($("report-window"), async () => {
  const data = {scope: $("report-scope").value};
  if (data.scope === "custom") {
    if (!$("report-start").value || !$("report-end").value) throw new Error("请选择完整起止时间");
    data.window_start = new Date($("report-start").value).toISOString();
    data.window_end = new Date($("report-end").value).toISOString();
  }
  await api("/api/reports/window", data);
  show("已生成本地简报，没有发送邮件。"); await load();
}));
sessionEstimate();
