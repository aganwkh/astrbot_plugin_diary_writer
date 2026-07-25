# v0.5 Long-Term Observability and Plugin Page Design

## Goal

Extend the v0.4 diary system with rebuildable yearly reviews, descriptive long-term trends, an opt-in private On This Day reminder, and one AstrBot Plugin Page. Daily metadata remains the sole factual source; all other outputs are derived and may be rebuilt.

## Boundaries

- No database, ORM, service process, frontend framework, build chain, or production connection.
- Trend and review code only reads daily JSON and existing derived review JSON. It never modifies daily metadata or continuity.
- Monthly material provides yearly narrative context only. Counts of events, topics, projects, moods, and unresolved items always derive from daily JSON, so a monthly summary cannot double-count a daily event.
- Plugin Page assets contain no diary body, evidence, identity data, credentials, or generated configuration. Private material is fetched on demand through authenticated Dashboard APIs.

## Yearly reviews

`ReviewService` gains the `yearly` kind with calendar-year period `YYYY`. Its daily coverage uses every day from 1 January through 31 December; metadata also stores `covered_periods` and `missing_periods` for calendar months.

An annual prompt receives compact daily factual material plus available monthly review metadata as labelled high-level context. It must not total monthly values. The parser stores direct daily date sources and optional monthly-period sources separately. Existing storage paths, pair writes, backup directories, locks, retry state, and stale fields work unchanged for `yearly`.

The automatic lifecycle creates a yearly review only for an ended year: successful or delayed persistence of 31 December daily data can create the year, and the normal review catch-up scans ended yearly periods. Manual yearly generation follows existing review behavior and may create a partial current-year review; its coverage fields make that explicit.

Core daily changes mark every containing weekly, monthly, and yearly review stale. A changed monthly review marks a yearly review that cited it stale. Technical fields still do not mark anything stale. No stale operation rewrites formal Markdown.

## Trends and reminders

`diary/trends.py` scans daily JSON at request time. It outputs dated mood-score points, monthly diary/event/unresolved counts, case-insensitive topic/project document frequencies, and month-to-month active-project observations. Empty and malformed optional fields yield stable empty values. "Event count" means the number of recorded daily metadata events; no clinical or importance judgement is inferred.

`reminder_state.json` records one local-date reminder, recipient, and timestamp. With `on_this_day_reminder_enabled=false` by default, it is unused. With it enabled, the private-message listener checks an authorized user's ordinary message only. Commands are ignored, groups never enter the path, no result emits nothing, and a successful same-day reminder is yielded without stopping propagation.

## Plugin Page

One Page lives in `pages/diary-manager/`, with `index.html`, `styles.css`, `api.js`, `state.js`, `render.js`, `charts.js`, and `app.js`. It uses `window.AstrBotPluginPage`, native DOM APIs, CSS variables, responsive grid layout, and simple SVG charts. It has no framework, local persistence, inline data payload, HTML interpolation, or Markdown-to-HTML rendering.

All displayed diary/LLM/API strings are inserted with `textContent` or text nodes. The API contracts return plain JSON; consumers never use `innerHTML` for dynamic data. Markdown is shown as text in v0.5. Future rich Markdown requires a reviewed sanitiser before implementation.

The backend registers plugin-local routes via `context.register_web_api()` and `astrbot.api.web`. Each handler rejects a missing `request.username`, validates enum/date/range/limit inputs, and returns only the requested material. The Dashboard bridge owns authentication; local tests verify the fail-closed handler rule, while real Dashboard middleware is marked for production validation.

`POST generate` supports daily/weekly/monthly/yearly backfill or forced rewrite. It requires an explicitly configured generation provider because a Page request has no private-event UMO. The existing date and period locks protect concurrent UI/cron/command requests.

## Offline validation

Tests cover year boundaries and missing months, monthly/daily non-double-counting, stale propagation, atomic failed rewrites, trends, reminder gating, Web API validation, text-only DOM rendering, and v0.4 fixture compatibility. Full unittest, compileall, and `git diff --check` remain the release gate.
