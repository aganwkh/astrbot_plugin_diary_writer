const state = { months: [], month: "", entries: [], entry: null, cache: new Map(), controller: null };
const $ = (id) => document.getElementById(id);
const status = $("status"), entry = $("entry"), days = $("days");

async function request(path, signal) {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`请求失败：${response.status}`);
  return response.json();
}

function dateText(value) { return value.replaceAll("-", "."); }
function setStatus(message) { status.textContent = message; status.hidden = !message; }

async function loadEntries(month, signal) {
  if (!state.cache.has(month)) state.cache.set(month, (await request(`/api/entries?month=${encodeURIComponent(month)}`, signal)).entries);
  return state.cache.get(month);
}

function preloadNeighbours() {
  const index = state.months.indexOf(state.month);
  for (const neighbour of [state.months[index - 1], state.months[index + 1]]) {
    if (neighbour && !state.cache.has(neighbour)) loadEntries(neighbour).catch(() => {});
  }
}

function renderDays() {
  days.replaceChildren(...state.entries.map((item) => {
    const button = document.createElement("button");
    button.className = `day${item.date === state.entry?.date ? " active" : ""}`;
    button.type = "button"; button.dataset.date = item.date;
    const date = document.createElement("strong"); date.textContent = item.date.slice(8);
    const title = document.createElement("small"); title.textContent = item.title;
    button.append(date, title); return button;
  }));
}

function renderMarkdown(markdown) {
  const output = $("markdown"); output.replaceChildren();
  for (const block of String(markdown || "").trim().split(/\n\s*\n/)) {
    const lines = block.split("\n"); const heading = lines[0].match(/^(#{1,3})\s+(.+)/);
    let node;
    if (heading) { node = document.createElement(`h${heading[1].length}`); node.textContent = heading[2]; }
    else if (lines.every((line) => /^[-*]\s+/.test(line))) { node = document.createElement("ul"); for (const line of lines) { const item = document.createElement("li"); item.textContent = line.replace(/^[-*]\s+/, ""); node.append(item); } }
    else if (lines.every((line) => /^>\s?/.test(line))) { node = document.createElement("blockquote"); node.textContent = lines.map((line) => line.replace(/^>\s?/, "")).join("\n"); }
    else { node = document.createElement("p"); node.textContent = lines.join("\n"); }
    if (node?.textContent) output.append(node);
  }
}

function renderEntry() {
  if (!state.entry) { entry.hidden = true; return; }
  entry.hidden = false; $("entry-date").textContent = dateText(state.entry.date);
  $("entry-title").textContent = state.entry.title; $("mood").textContent = state.entry.mood || "日记";
  renderMarkdown(state.entry.markdown); const tags = $("tags"); tags.replaceChildren();
  for (const tag of state.entry.tags) { const pill = document.createElement("span"); pill.className = "tag"; pill.textContent = tag; tags.append(pill); }
  renderDays(); const current = days.querySelector(".active"); current?.scrollIntoView({ block: "nearest", inline: "center" });
  const index = state.entries.findIndex((item) => item.date === state.entry.date);
  $("next-entry").disabled = index <= 0; $("previous-entry").disabled = index === state.entries.length - 1;
}

async function openEntry(item, signal) {
  setStatus("正在翻到这一天…"); entry.hidden = true;
  state.entry = (await request(`/api/entries/${item.date}`, signal)).entry;
  setStatus(""); renderEntry();
}

async function openMonth(month, preferredDate = "") {
  state.controller?.abort(); state.controller = new AbortController(); const { signal } = state.controller;
  try {
    state.month = month; $("month-label").textContent = month.replace("-", " 年 ") + " 月";
    state.entries = await loadEntries(month, signal); preloadNeighbours();
    if (!state.entries.length) { state.entry = null; renderEntry(); setStatus("这个月还没有日记。"); return; }
    await openEntry(state.entries.find((item) => item.date === preferredDate) || state.entries[0], signal);
  } catch (error) { if (error.name !== "AbortError") { state.entry = null; renderEntry(); setStatus("日记暂时打不开，请稍后再试。"); } }
}

function changeMonth(delta) { const target = state.months[state.months.indexOf(state.month) + delta]; if (target) openMonth(target); }
function changeEntry(delta) { const index = state.entries.findIndex((item) => item.date === state.entry?.date); const target = state.entries[index + delta]; if (target) openEntry(target, state.controller?.signal).catch(() => {}); }

days.addEventListener("click", (event) => { const button = event.target.closest("button[data-date]"); const target = state.entries.find((item) => item.date === button?.dataset.date); if (target) openEntry(target, state.controller?.signal).catch(() => {}); });
$("previous-month").addEventListener("click", () => changeMonth(1)); $("next-month").addEventListener("click", () => changeMonth(-1));
$("previous-entry").addEventListener("click", () => changeEntry(1)); $("next-entry").addEventListener("click", () => changeEntry(-1));

async function start() {
  try { state.months = (await request("/api/months")).months; if (!state.months.length) { setStatus("还没有可以阅读的日记。"); return; } await openMonth(state.months[0]); }
  catch { setStatus("日记站暂时无法连接，请稍后再试。"); }
}
start();
