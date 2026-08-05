import { lineChart } from "./charts.js";

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function replace(target, ...nodes) {
  target.replaceChildren(...nodes);
}

function button(text, handler, className = "button") {
  const node = element("button", className, text);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function list(items, renderItem) {
  const node = element("ul", "result-list");
  for (const item of items) {
    const row = element("li", "result-row");
    renderItem(row, item);
    node.append(row);
  }
  return node;
}

function summaryRow(label, value) {
  const row = element("div", "metric");
  row.append(element("span", "metric-label", label), element("strong", "metric-value", String(value ?? 0)));
  return row;
}

export function renderShell(state, t) {
  document.title = t("pages.diary-manager.title", "日记管理");
  document.getElementById("page-title").textContent = t("pages.diary-manager.heading", "日记管理");
  document.getElementById("page-description").textContent = t("pages.diary-manager.description", "查看已保存的日记。");
  document.getElementById("refresh").textContent = t("pages.diary-manager.refresh", "刷新");
  document.querySelectorAll("[data-panel]").forEach((panel) => panel.classList.toggle("is-hidden", panel.dataset.panel !== state.activeTab));
  document.querySelectorAll("[data-tab]").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.tab === state.activeTab));
  document.getElementById("notice").textContent = state.notice || "";
}

export function renderOverview(state) {
  const target = document.getElementById("overview-panel");
  const data = state.overview;
  if (!data) return replace(target, element("p", "muted", "正在加载运行状态…"));
  const section = element("div", "stack");
  const counts = element("div", "metric-grid");
  Object.entries(data.counts || {}).forEach(([kind, count]) => counts.append(summaryRow(`${kind} 日记`, count)));
  section.append(counts);
  const settings = element("p", "muted", `自动写日记：${data.auto_write_enabled ? "已开启" : "已关闭"}；生成 Provider：${data.generation_provider_configured ? "已配置" : "未配置"}`);
  section.append(settings);
  const failures = [data.generation_state].filter((item) => item && item.failed);
  section.append(element("h2", "section-title", "生成状态"));
  section.append(failures.length ? list(failures, (row, item) => row.append(element("strong", "", item.pending_date || item.pending_period || "待处理"), element("span", "muted", `失败，已重试 ${item.retry_count || 0} 次`))) : element("p", "muted", "没有待处理的失败生成。"));
  replace(target, section);
}

export function renderTimeline(state, onEntry) {
  const target = document.getElementById("timeline-panel");
  const section = element("div", "stack");
  section.append(element("h2", "section-title", "日历 / 时间线"));
  section.append(state.calendar.length ? list(state.calendar, (row, item) => {
    const detail = element("div", "row-copy");
    const mode = item.entry_type || "normal";
    const activity = mode === "low_activity" ? `；${item.activity_round_count || 0} 轮私聊、${item.conversation_source_count || 0} 条聊天素材、${item.historical_memory_source_count || 0} 条回忆` : "";
    detail.append(element("strong", "", `${item.date} · ${item.title || "无标题"}`), element("span", "muted", `${mode} · ${item.event_count || 0} 个事件${activity}`));
    row.append(detail, button("查看", () => onEntry("daily", item.date), "button secondary"));
  }) : element("p", "muted", "没有可展示的日记。"));
  const browse = element("form", "generate-form");
  const kind = document.createElement("select");
  ["daily"].forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    kind.append(option);
  });
  const period = document.createElement("input");
  period.required = true;
  period.placeholder = "输入日期或周期";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "button secondary";
  submit.textContent = "浏览指定条目";
  browse.append(kind, period, submit);
  browse.addEventListener("submit", (event) => {
    event.preventDefault();
    onEntry(kind.value, period.value.trim());
  });
  section.append(element("h2", "section-title", "浏览日记"), browse);
  replace(target, section);
}

export function renderSearch(onSearch, onEntry, onEntity) {
  const target = document.getElementById("search-panel");
  const form = element("form", "search-form");
  const input = document.createElement("input");
  input.type = "search";
  input.name = "q";
  input.maxLength = 200;
  input.placeholder = "关键词、项目或话题";
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "button";
  submit.textContent = "搜索";
  form.append(input, submit);
  const results = element("div", "stack");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await onSearch(input.value, results, onEntry);
  });
  const entities = element("div", "entity-grid");
  ["topics", "projects", "people"].forEach((field) => entities.append(button(`查看 ${field}`, () => onEntity(field), "button secondary")));
  replace(target, form, entities, results);
}

export function renderSearchResults(target, data, onEntry) {
  const results = data.results || [];
  replace(target, results.length ? list(results, (row, item) => {
    const detail = element("div", "row-copy");
    detail.append(element("strong", "", `${item.date} · ${item.title || "无标题"}`), element("span", "muted", item.summary || ""));
    row.append(detail, button("查看", () => onEntry("daily", item.date), "button secondary"));
  }) : element("p", "muted", "未找到匹配的日记。"));
}

export function renderEntities(target, field, data, onTimeline) {
  const values = data.values || [];
  replace(target, element("h2", "section-title", field), values.length ? list(values, (row, item) => row.append(button(`${item.name} (${item.count})`, () => onTimeline(field, item.name), "link-button"))) : element("p", "muted", "没有可显示的条目。"));
}

export function renderEntityTimeline(target, data, onEntry) {
  const entries = data.entries || [];
  replace(target, element("h2", "section-title", `${data.field}: ${data.value}`), entries.length ? list(entries, (row, item) => row.append(button(`${item.date} · ${item.title || "无标题"}`, () => onEntry("daily", item.date), "link-button"))) : element("p", "muted", "未找到相关日记。"));
}

export function renderTrends(state) {
  const target = document.getElementById("trends-panel");
  const data = state.trends;
  if (!data) return replace(target, element("p", "muted", "正在加载趋势…"));
  const section = element("div", "stack");
  section.append(element("h2", "section-title", "情绪评分趋势"), lineChart(data.mood_points || [], "mood_score", "情绪评分趋势"));
  const monthly = element("div", "metric-grid");
  (data.monthly || []).forEach((item) => monthly.append(summaryRow(`${item.month} 日记`, item.diary_count)));
  section.append(element("h2", "section-title", "每月日记数量"), monthly);
  ["topics", "projects"].forEach((field) => section.append(element("h2", "section-title", `高频 ${field}`), list((data[field] || []).slice(0, 10), (row, item) => row.append(element("span", "", item.name), element("strong", "", item.count)))));
  section.append(element("h2", "section-title", "活跃项目变化"));
  section.append((data.project_activity || []).length ? list(data.project_activity, (row, item) => {
    const observed = (item.observed || []).join("、") || "无";
    const added = (item.added || []).join("、") || "无新增";
    row.append(element("strong", "", item.month), element("span", "muted", `观察到：${observed}；新增：${added}`));
  }) : element("p", "muted", "暂无项目活动数据。"));
  replace(target, section);
}

export function renderGenerate(onGenerate) {
  const target = document.getElementById("generate-panel");
  const form = element("form", "generate-form");
  const kind = document.createElement("select");
  kind.name = "kind";
  ["daily"].forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    kind.append(option);
  });
  const period = document.createElement("input");
  period.name = "period";
  period.required = true;
  period.placeholder = "2025-01-01 / 2025-W01 / 2025-01 / 2025";
  const force = document.createElement("input");
  force.type = "checkbox";
  force.name = "force";
  const forceLabel = element("label", "checkbox");
  forceLabel.append(force, document.createTextNode("重写（保留旧版本备份）"));
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "button";
  submit.textContent = "开始生成";
  form.append(kind, period, forceLabel, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await onGenerate(kind.value, period.value.trim(), force.checked);
  });
  replace(target, element("p", "muted", "生成只使用服务器配置的 Provider。"), form);
}

export function renderEntry(state, onClose) {
  const target = document.getElementById("entry-panel");
  if (!state.entry) {
    target.classList.add("is-hidden");
    return replace(target);
  }
  target.classList.remove("is-hidden");
  const data = state.entry;
  const title = element("h2", "section-title", `${data.kind} · ${data.period}`);
  const close = button("关闭", onClose, "button secondary");
  const header = element("div", "entry-header");
  header.append(title, close);
  const markdown = element("pre", "entry-text");
  markdown.textContent = data.markdown || "该条目没有 Markdown 正文。";
  const metadata = element("pre", "metadata-text");
  metadata.textContent = JSON.stringify(data.metadata || {}, null, 2);
  replace(target, header, markdown, element("h3", "section-title", "Metadata / evidence"), metadata);
}
