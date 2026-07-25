import { diaryApi } from "./api.js";

function node(tag, text = "", className = "") {
  const value = document.createElement(tag);
  if (className) value.className = className;
  value.textContent = text;
  return value;
}

function button(text, handler, className = "button secondary") {
  const value = node("button", text, className);
  value.type = "button";
  value.addEventListener("click", handler);
  return value;
}

function formRow(...children) {
  const value = document.createElement("form");
  value.className = "generate-form";
  value.append(...children);
  return value;
}

function input(placeholder, type = "text") {
  const value = document.createElement("input");
  value.type = type;
  value.placeholder = placeholder;
  value.required = true;
  return value;
}

function select(values, placeholder) {
  const value = document.createElement("select");
  const first = node("option", placeholder);
  first.value = "";
  value.append(first);
  for (const item of values) {
    const option = node("option", item);
    option.value = item;
    value.append(option);
  }
  return value;
}

function output(target, value) {
  const pre = node("pre", "", "metadata-text");
  pre.textContent = JSON.stringify(value, null, 2);
  target.replaceChildren(pre);
}

function section(title) {
  const value = node("section", "", "stack");
  value.append(node("h2", title, "section-title"));
  return value;
}

export function renderV06(onNotice) {
  const target = document.getElementById("manage-panel");
  const root = node("div", "", "stack");
  root.append(historySection(onNotice), archiveSection(onNotice), lifecycleSection(onNotice), reflectionSection(onNotice), integritySection(onNotice));
  target.replaceChildren(root);
}

function historySection(onNotice) {
  const container = section("纠错与修订历史");
  const diaryDate = input("YYYY-MM-DD", "date");
  const field = select(["title", "mood", "topics", "tags", "people", "projects", "highlights", "unresolved", "ongoing_topics"], "选择字段");
  const oldValue = input("精确旧值");
  const newValue = input("精确新值");
  const eventFact = select([], "读取日期后选择 event / fact");
  eventFact.disabled = true;
  const result = node("div", "", "stack");
  const correct = button("保存确定性纠错", async () => {
    try {
      const selected = eventFact.value ? JSON.parse(eventFact.value) : null;
      const body = selected
        ? { date: diaryDate.value, event_id: selected.event_id, fact_id: selected.fact_id, old_value: selected.old_value, new_value: newValue.value }
        : { date: diaryDate.value, field: field.value, old_value: oldValue.value, new_value: newValue.value };
      const reply = await diaryApi.correct(body);
      onNotice("纠错已保存。"); output(result, reply);
    } catch (error) { onNotice(error.message); }
  });
  const load = button("读取历史", async () => {
    try {
      const [corrections, revisions, entry] = await Promise.all([
        diaryApi.corrections(diaryDate.value), diaryApi.revisions(diaryDate.value), diaryApi.entry("daily", diaryDate.value),
      ]);
      eventFact.replaceChildren(node("option", "选择 event / fact"));
      for (const event of entry.metadata?.events || []) {
        for (const fact of event.fact_records || []) {
          if (!event.event_id || !fact.fact_id || !fact.value) continue;
          const option = node("option", `${event.summary || "事件"} — ${fact.value}`);
          option.value = JSON.stringify({ event_id: event.event_id, fact_id: fact.fact_id, old_value: fact.value });
          eventFact.append(option);
        }
      }
      eventFact.disabled = eventFact.options.length <= 1;
      const rows = node("div", "", "stack");
      rows.append(node("p", `当前 revision：${revisions.current_revision_id || "无"}`, "muted"));
      for (const item of revisions.revisions || []) {
        const row = node("div", "", "result-row");
        row.append(node("span", `${item.id} · ${item.operation}`), button("查看", async () => {
          try { output(result, await diaryApi.revision(diaryDate.value, item.id)); } catch (error) { onNotice(error.message); }
        }), button("与当前比较", async () => {
          try { output(result, await diaryApi.revisionDiff(diaryDate.value, item.id, revisions.current_revision_id)); } catch (error) { onNotice(error.message); }
        }), button("回滚到此版本", async () => {
          try { output(result, await diaryApi.rollback(diaryDate.value, item.id)); onNotice("回滚已完成。"); } catch (error) { onNotice(error.message); }
        }));
        rows.append(row);
      }
      const details = node("pre", "", "metadata-text"); details.textContent = JSON.stringify(corrections.corrections || [], null, 2);
      rows.append(details); result.replaceChildren(rows);
    } catch (error) { onNotice(error.message); }
  });
  eventFact.addEventListener("change", () => {
    if (!eventFact.value) { oldValue.readOnly = false; return; }
    oldValue.value = JSON.parse(eventFact.value).old_value;
    oldValue.readOnly = true;
  });
  const form = formRow(diaryDate, field, oldValue, newValue, eventFact, correct, load);
  form.addEventListener("submit", (event) => event.preventDefault());
  container.append(form, result); return container;
}

function archiveSection(onNotice) {
  const container = section("备份、校验与恢复");
  const result = node("div", "", "stack");
  const refresh = async () => {
    try {
      const reply = await diaryApi.archives();
      const rows = node("div", "", "stack");
      for (const item of reply.archives || []) {
        const row = node("div", "", "result-row");
        row.append(node("span", `${item.name} (${item.size} bytes)`),
          button("校验", async () => { try { output(result, await diaryApi.archiveVerify(item.name)); } catch (error) { onNotice(error.message); } }),
          button("下载", async () => { try { await diaryApi.archiveDownload(item.name); onNotice("下载已开始。"); } catch (error) { onNotice(error.message); } }),
          button("演练恢复", async () => { try { output(result, await diaryApi.archiveRestore(item.name, true)); } catch (error) { onNotice(error.message); } }),
          button("恢复", async () => { if (!window.confirm("恢复会先创建当前数据快照。继续？")) return; try { output(result, await diaryApi.archiveRestore(item.name, false)); onNotice("恢复已完成。"); } catch (error) { onNotice(error.message); } }));
        rows.append(row);
      }
      result.replaceChildren(rows.childNodes.length ? rows : node("p", "还没有可用备份。", "muted"));
    } catch (error) { onNotice(error.message); }
  };
  container.append(button("创建 ZIP 备份", async () => { try { await diaryApi.archiveExport(); onNotice("备份已创建。"); await refresh(); } catch (error) { onNotice(error.message); } }), button("刷新备份列表", refresh), result);
  return container;
}

function lifecycleSection(onNotice) {
  const container = section("人物 / 项目生命周期");
  const field = document.createElement("select");
  for (const value of ["people", "projects"]) { const option = node("option", value); option.value = value; field.append(option); }
  const value = input("人物或项目名称"); const result = node("div", "", "stack");
  const form = formRow(field, value, button("查看轨迹", async () => { try { output(result, await diaryApi.lifecycle(field.value, value.value)); } catch (error) { onNotice(error.message); } }));
  form.addEventListener("submit", (event) => event.preventDefault()); container.append(form, result); return container;
}

function reflectionSection(onNotice) {
  const container = section("角色主观观察（不参与事实统计）");
  const kind = document.createElement("select"); ["monthly", "yearly"].forEach((value) => { const option = node("option", value); option.value = value; kind.append(option); });
  const period = input("YYYY-MM 或 YYYY"); const result = node("div", "", "stack");
  const form = formRow(kind, period, button("查看", async () => { try { output(result, await diaryApi.reflections(kind.value, period.value)); } catch (error) { onNotice(error.message); } }), button("生成", async () => { try { output(result, await diaryApi.reflectionGenerate(kind.value, period.value, false)); onNotice("角色观察已生成。"); } catch (error) { onNotice(error.message); } }));
  form.addEventListener("submit", (event) => event.preventDefault()); container.append(form, result); return container;
}

function integritySection(onNotice) {
  const container = section("完整性检查"); const result = node("div", "", "stack");
  container.append(button("只检查", async () => { try { output(result, await diaryApi.integrity()); } catch (error) { onNotice(error.message); } }), button("安全修复", async () => { try { output(result, await diaryApi.integrityRepair()); onNotice("安全修复已完成。"); } catch (error) { onNotice(error.message); } }), result);
  return container;
}
