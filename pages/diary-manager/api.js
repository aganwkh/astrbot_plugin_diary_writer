const bridge = window.AstrBotPluginPage;
const ROOT = "diary-manager";

function endpoint(path) {
  return `${ROOT}/${path}`;
}

function requireBridge() {
  if (!bridge) {
    throw new Error("AstrBot Plugin Page bridge is unavailable");
  }
  return bridge;
}

export const diaryApi = Object.freeze({
  ready: () => requireBridge().ready(),
  translate: (key, fallback) => requireBridge().t(key, fallback),
  onContext: (handler) => requireBridge().onContext(handler),
  overview: () => requireBridge().apiGet(endpoint("overview")),
  calendar: (params = {}) => requireBridge().apiGet(endpoint("calendar"), params),
  entry: (kind, period) => requireBridge().apiGet(endpoint("entry"), { kind, period }),
  search: (q) => requireBridge().apiGet(endpoint("search"), { q, limit: 50 }),
  entities: (field) => requireBridge().apiGet(endpoint("entities"), { field, limit: 50 }),
  timeline: (field, value) => requireBridge().apiGet(endpoint("timeline"), { field, value }),
  trends: () => requireBridge().apiGet(endpoint("trends")),
  generate: (kind, period, force) => requireBridge().apiPost(endpoint("generate"), { kind, period, force }),
});
