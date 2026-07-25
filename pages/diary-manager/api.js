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
  corrections: (date) => requireBridge().apiGet(endpoint("corrections"), { date }),
  correct: (body) => requireBridge().apiPost(endpoint("correction"), body),
  revisions: (date) => requireBridge().apiGet(endpoint("revisions"), { date }),
  revision: (date, revision_id) => requireBridge().apiGet(endpoint("revision"), { date, revision_id }),
  revisionDiff: (date, from, to) => requireBridge().apiGet(endpoint("revision-diff"), { date, from, to }),
  rollback: (date, revision_id) => requireBridge().apiPost(endpoint("rollback"), { date, revision_id }),
  archives: () => requireBridge().apiGet(endpoint("archives")),
  archiveExport: () => requireBridge().apiPost(endpoint("archive-export"), {}),
  archiveVerify: (archive) => requireBridge().apiGet(endpoint("archive-verify"), { archive }),
  archiveDownload: (archive) => requireBridge().download(endpoint("archive-download"), { archive }, archive),
  archiveRestore: (archive, dry_run) => requireBridge().apiPost(endpoint("archive-restore"), { archive, dry_run }),
  lifecycle: (field, value) => requireBridge().apiGet(endpoint("lifecycle"), { field, value }),
  lifecycles: (field) => requireBridge().apiGet(endpoint("lifecycles"), { field }),
  reflections: (kind, period) => requireBridge().apiGet(endpoint("reflections"), { kind, period }),
  reflectionGenerate: (kind, period, force) => requireBridge().apiPost(endpoint("reflection-generate"), { kind, period, force }),
  integrity: () => requireBridge().apiGet(endpoint("integrity")),
  integrityRepair: () => requireBridge().apiPost(endpoint("integrity-repair"), {}),
});
