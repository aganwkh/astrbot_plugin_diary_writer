import { diaryApi } from "./api.js";
import { clearEntry, getState, updateState } from "./state.js";
import {
  renderEntities, renderEntityTimeline, renderEntry, renderGenerate, renderOverview,
  renderSearch, renderSearchResults, renderShell, renderTimeline, renderTrends,
} from "./render.js";
import { renderV06 } from "./v06.js";

function setNotice(message) {
  updateState({ notice: message });
  renderShell(getState(), diaryApi.translate);
}

function errorMessage(error) {
  return error instanceof Error ? error.message : "操作失败，请检查插件运行状态。";
}

async function refresh() {
  setNotice("正在刷新…");
  try {
    const [overview, calendar] = await Promise.all([diaryApi.overview(), diaryApi.calendar()]);
    updateState({ overview, calendar: calendar.entries || [], notice: "" });
    renderAll();
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

async function openEntry(kind, period) {
  try {
    setNotice("正在读取条目…");
    updateState({ entry: await diaryApi.entry(kind, period), notice: "" });
    renderEntry(getState(), closeEntry);
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

function closeEntry() {
  clearEntry();
  renderEntry(getState(), closeEntry);
}

async function search(query, target, onEntry) {
  if (!query.trim()) return;
  try {
    renderSearchResults(target, await diaryApi.search(query.trim()), onEntry);
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

async function showEntities(field) {
  const target = document.getElementById("search-panel").querySelector(".stack");
  try {
    renderEntities(target, field, await diaryApi.entities(field), showEntityTimeline);
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

async function showEntityTimeline(field, value) {
  const target = document.getElementById("search-panel").querySelector(".stack");
  try {
    renderEntityTimeline(target, await diaryApi.timeline(field, value), openEntry);
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

async function loadTrends() {
  if (getState().trends) return;
  try {
    updateState({ trends: await diaryApi.trends() });
    renderTrends(getState());
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

async function generate(kind, period, force) {
  try {
    setNotice("正在生成…");
    await diaryApi.generate(kind, period, force);
    await refresh();
    setNotice("生成完成。");
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

function renderAll() {
  const state = getState();
  renderShell(state, diaryApi.translate);
  renderOverview(state);
  renderTimeline(state, openEntry);
  renderSearch(search, openEntry, showEntities);
  renderTrends(state);
  renderGenerate(generate);
  renderV06(setNotice);
  renderEntry(state, closeEntry);
}

function activate(tab) {
  updateState({ activeTab: tab });
  renderShell(getState(), diaryApi.translate);
  if (tab === "trends") loadTrends();
}

async function start() {
  try {
    updateState({ context: await diaryApi.ready() });
    document.getElementById("tabs").addEventListener("click", (event) => {
      const tab = event.target.closest("[data-tab]");
      if (tab) activate(tab.dataset.tab);
    });
    document.getElementById("refresh").addEventListener("click", refresh);
    diaryApi.onContext(() => renderShell(getState(), diaryApi.translate));
    await refresh();
  } catch (error) {
    setNotice(errorMessage(error));
  }
}

start();
