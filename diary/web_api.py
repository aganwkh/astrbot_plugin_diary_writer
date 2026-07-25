"""Authenticated, read-mostly WebUI API for the Diary Writer Plugin Page."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from .retrieval import search_daily, timeline
from .service import diary_changed
from .storage import DiaryStorage
from .trends import build_trends
from .corrections import CorrectionError, CorrectionService
from .archives import ArchiveError, ArchiveService
from .lifecycle import lifecycle, all_lifecycles
from .reflections import ReflectionService
from .integrity import IntegrityAudit

try:  # Keep the service modules importable by the offline test suite.
    from astrbot.api.web import error_response, file_response, json_response, request
except ImportError:  # pragma: no cover - production always provides astrbot.api.web
    request = None

    def json_response(value: Any):
        return value

    def error_response(message: str, status_code: int = 400):
        return {"error": message, "status_code": status_code}

    def file_response(path: Path, filename: str, content_type: str):
        return {"path": str(path), "filename": filename, "content_type": content_type}


PLUGIN_NAME = "astrbot_plugin_diary_writer"
KINDS = frozenset({"daily", "weekly", "monthly", "yearly"})
ENTITY_FIELDS = frozenset({"topics", "projects", "people"})
MAX_LIMIT = 100
MAX_QUERY_LENGTH = 200
MAX_CALENDAR_DAYS = 366
MAX_TREND_DAYS = 3660
MAX_ARCHIVE_NAME = re.compile(r"(?:diary-export|pre-restore)-\d{8}T\d{12}Z\.zip")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _public_generation_state(value: dict[str, Any], pending_key: str) -> dict[str, Any]:
    """Expose progress only; provider errors can contain paths, prompts, or credentials."""
    def item(raw: Any) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        stage = str(raw.get("stage") or "idle")
        retry_count = raw.get("retry_count", 0)
        return {
            pending_key: str(raw.get(pending_key) or ""), "stage": stage,
            "retry_count": retry_count if isinstance(retry_count, int) and retry_count >= 0 else 0,
            "updated_at": str(raw.get("updated_at") or ""), "last_success_at": str(raw.get("last_success_at") or ""),
            "failed": stage == "failed",
        }

    public = item(value)
    if pending_key == "pending_period":
        entries = value.get("entries")
        if isinstance(entries, dict):
            public["entries"] = [item(entry) for entry in entries.values() if isinstance(entry, dict)]
    return public


def _period(kind: str, value: Any) -> str:
    value = str(value or "")
    if kind not in KINDS:
        raise ValueError("invalid kind")
    if kind == "daily":
        date.fromisoformat(value)
    elif kind == "weekly":
        match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
        if not match:
            raise ValueError("weekly period must be YYYY-Www")
        date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    elif kind == "monthly":
        match = re.fullmatch(r"(\d{4})-(\d{2})", value)
        if not match:
            raise ValueError("monthly period must be YYYY-MM")
        date(int(match.group(1)), int(match.group(2)), 1)
    else:
        if not re.fullmatch(r"\d{4}", value):
            raise ValueError("yearly period must be YYYY")
        date(int(value), 1, 1)
    return value


def _date_range(start: Any, end: Any, maximum: int) -> tuple[str | None, str | None]:
    if not start and not end:
        return None, None
    try:
        start_date = date.fromisoformat(str(start)) if start else None
        end_date = date.fromisoformat(str(end)) if end else None
    except ValueError as exc:
        raise ValueError("dates must be YYYY-MM-DD") from exc
    if start_date and end_date:
        if start_date > end_date:
            raise ValueError("start must not be after end")
        if (end_date - start_date).days > maximum:
            raise ValueError(f"date range must not exceed {maximum} days")
    return start_date.isoformat() if start_date else None, end_date.isoformat() if end_date else None


class DiaryWebApi:
    """Small adapter between AstrBot's authenticated Web APIs and diary services."""

    def __init__(
        self,
        context: Any,
        config: Any,
        storage: DiaryStorage,
        service: Any,
        reviews: Any,
        after_daily: Callable[[date, Any, str], Awaitable[None]] | None = None,
    ):
        self.context, self.config, self.storage = context, config, storage
        self.service, self.reviews, self.after_daily = service, reviews, after_daily
        self.corrections = CorrectionService(storage, reviews)
        self.archives = ArchiveService(storage, {
            key: getattr(config, key) for key in (
                "persona_preset", "persona_name", "user_nickname", "diary_voice", "auto_write_enabled",
                "inactive_minutes", "fallback_inactive_minutes", "cron_start_delay_minutes", "on_this_day_reminder_enabled",
            ) if hasattr(config, key)
        })
        self.reflections = ReflectionService(storage, config)
        self.integrity = IntegrityAudit(storage)

    def register(self) -> None:
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            return
        routes = (
            ("overview", self.overview, ["GET"], "Diary Writer overview"),
            ("calendar", self.calendar, ["GET"], "Diary Writer calendar"),
            ("entry", self.entry, ["GET"], "Diary Writer entry"),
            ("search", self.search, ["GET"], "Diary Writer search"),
            ("entities", self.entities, ["GET"], "Diary Writer entities"),
            ("timeline", self.timeline, ["GET"], "Diary Writer timeline"),
            ("trends", self.trends, ["GET"], "Diary Writer trends"),
            ("generate", self.generate, ["POST"], "Generate Diary Writer entry"),
            ("corrections", self.corrections_list, ["GET"], "Diary Writer corrections"),
            ("correction", self.correction, ["POST"], "Correct a diary fact"),
            ("revisions", self.revisions, ["GET"], "Diary Writer revisions"),
            ("revision", self.revision, ["GET"], "Diary Writer revision"),
            ("revision-diff", self.revision_diff, ["GET"], "Diary Writer revision diff"),
            ("rollback", self.rollback, ["POST"], "Rollback a diary revision"),
            ("archives", self.archives_list, ["GET"], "Diary Writer archives"),
            ("archive-export", self.archive_export, ["POST"], "Export diary archive"),
            ("archive-verify", self.archive_verify, ["GET"], "Verify diary archive"),
            ("archive-download", self.archive_download, ["GET"], "Download diary archive"),
            ("archive-restore", self.archive_restore, ["POST"], "Restore diary archive"),
            ("lifecycle", self.lifecycle, ["GET"], "Diary Writer lifecycle"),
            ("lifecycles", self.lifecycles, ["GET"], "Diary Writer lifecycle list"),
            ("reflections", self.reflections_list, ["GET"], "Diary Writer reflections"),
            ("reflection-generate", self.reflection_generate, ["POST"], "Generate diary reflection"),
            ("integrity", self.integrity_check, ["GET"], "Diary Writer integrity"),
            ("integrity-repair", self.integrity_repair, ["POST"], "Safely repair diary metadata"),
        )
        for suffix, handler, methods, description in routes:
            register(f"/{PLUGIN_NAME}/diary-manager/{suffix}", handler, methods, description)

    @staticmethod
    def _identity_error():
        if request is None or not str(getattr(request, "username", "") or "").strip():
            return error_response("Dashboard authentication is required", status_code=401)
        return None

    @staticmethod
    def _query(name: str, default: Any = None) -> Any:
        return request.query.get(name, default) if request is not None else default

    @staticmethod
    async def _payload() -> dict[str, Any] | None:
        try:
            value = await request.json(default={}) if request is not None else {}
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _archive_path(storage: DiaryStorage, value: Any) -> Path:
        name = str(value or "")
        if not MAX_ARCHIVE_NAME.fullmatch(name):
            raise ValueError("invalid archive name")
        roots = (storage.root / "archive_exports", storage.root / "pre_restore_snapshots")
        found = []
        for root in roots:
            path = root / name
            # is_file follows links, so a valid basename is not sufficient.
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.resolve().parent != root.resolve():
                    continue
            except OSError:
                continue
            found.append(path)
        if len(found) != 1:
            raise ValueError("archive not found")
        return found[0]

    async def overview(self):
        if (denied := self._identity_error()) is not None:
            return denied
        daily = list(self.storage.iter_daily_metadata() or ())
        reviews = list(self.storage.iter_review_metadata() or ())
        counts = {"daily": len(daily), "weekly": 0, "monthly": 0, "yearly": 0}
        stale = []
        for kind, period, metadata in reviews:
            if kind in counts:
                counts[kind] += 1
            if metadata.get("summary_stale"):
                stale.append({"kind": kind, "period": period, "stale_reason": str(metadata.get("stale_reason") or "")})
        return json_response({
            "counts": counts,
            "stale_summaries": stale,
            "generation_state": _public_generation_state(_load_json(self.storage.state_path), "pending_date"),
            "review_generation_state": _public_generation_state(_load_json(self.storage.review_state_path), "pending_period"),
            "auto_write_enabled": bool(getattr(self.config, "can_auto_write", False)),
            "generation_provider_configured": bool(getattr(self.config, "generation_provider_id", "")),
        })

    async def calendar(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            start, end = _date_range(self._query("from"), self._query("to"), MAX_CALENDAR_DAYS)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if start is None and end is None:
            today = date.today()
            start, end = (today - timedelta(days=MAX_CALENDAR_DAYS - 1)).isoformat(), today.isoformat()
        elif start is None:
            start = (date.fromisoformat(end) - timedelta(days=MAX_CALENDAR_DAYS)).isoformat()
        elif end is None:
            end = (date.fromisoformat(start) + timedelta(days=MAX_CALENDAR_DAYS)).isoformat()
        entries = []
        for item in self.storage.iter_daily_metadata() or ():
            value = str(item.get("date") or "")
            if (start and value < start) or (end and value > end):
                continue
            events = item.get("events") if isinstance(item.get("events"), list) else []
            entries.append({
                "date": value, "title": str(item.get("title") or ""),
                "event_count": sum(isinstance(event, dict) for event in events),
            })
        return json_response({"from": start, "to": end, "entries": sorted(entries, key=lambda item: item["date"], reverse=True)})

    async def entry(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            kind = str(self._query("kind") or "")
            period = _period(kind, self._query("period"))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if kind == "daily":
            metadata, markdown_path = self.storage.load_metadata(period), self.storage.diary_path(period)
        else:
            metadata, markdown_path = self.storage.load_review_metadata(kind, period), self.storage.review_path(kind, period)
        if metadata is None and not markdown_path.is_file():
            return error_response("entry not found", status_code=404)
        try:
            markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
        except OSError:
            return error_response("entry could not be read", status_code=500)
        return json_response({"kind": kind, "period": period, "markdown": markdown, "metadata": metadata or {}})

    async def search(self):
        if (denied := self._identity_error()) is not None:
            return denied
        query = str(self._query("q") or "").strip()
        try:
            limit = _limit(self._query("limit", 20))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if not query or len(query) > MAX_QUERY_LENGTH:
            return error_response("q must contain 1 to 200 characters", status_code=400)
        results = search_daily(self.storage, query)[:limit]
        return json_response({"query": query, "results": [_reference(item) for item in results]})

    async def entities(self):
        if (denied := self._identity_error()) is not None:
            return denied
        field = str(self._query("field") or "")
        if field not in ENTITY_FIELDS:
            return error_response("field must be topics, projects, or people", status_code=400)
        try:
            limit = _limit(self._query("limit", 50))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        counts: Counter[str] = Counter()
        names: dict[str, str] = {}
        for item in self.storage.iter_daily_metadata() or ():
            for value in _texts(item.get(field)):
                key = value.casefold()
                counts[key] += 1
                names.setdefault(key, value)
        values = [{"name": names[key], "count": count} for key, count in sorted(counts.items(), key=lambda item: (-item[1], names[item[0]].casefold()))[:limit]]
        return json_response({"field": field, "values": values})

    async def timeline(self):
        if (denied := self._identity_error()) is not None:
            return denied
        field, value = str(self._query("field") or ""), str(self._query("value") or "").strip()
        if field not in {"topics", "projects"}:
            return error_response("field must be topics or projects", status_code=400)
        if not value or len(value) > MAX_QUERY_LENGTH:
            return error_response("value must contain 1 to 200 characters", status_code=400)
        result = timeline(self.storage, value, field)
        if result is None:
            return json_response({"field": field, "value": value, "entries": []})
        return json_response({"field": field, "value": value, "first": _reference(result.first), "latest": _reference(result.latest), "entries": [_reference(item) for item in result.entries]})

    async def trends(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            start, end = _date_range(self._query("from"), self._query("to"), MAX_TREND_DAYS)
            return json_response(build_trends(self.storage, start, end))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def corrections_list(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            diary_date = _period("daily", self._query("date"))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"date": diary_date, "corrections": list(self.storage.iter_corrections(diary_date) or ())})

    async def correction(self):
        if (denied := self._identity_error()) is not None:
            return denied
        payload = await self._payload()
        if payload is None:
            return error_response("JSON object required", status_code=400)
        try:
            diary_date = _period("daily", payload.get("date"))
            old_value, new_value = _required_text(payload.get("old_value"), "old_value"), _required_text(payload.get("new_value"), "new_value")
            event_id, fact_id = str(payload.get("event_id") or ""), str(payload.get("fact_id") or "")
            if bool(event_id) != bool(fact_id):
                raise ValueError("event_id and fact_id must be supplied together")
            if event_id:
                result = await self.corrections.replace_event_fact(diary_date, event_id, fact_id, old_value, new_value, source="webui")
            else:
                result = await self.corrections.replace(diary_date, _required_text(payload.get("field"), "field"), old_value, new_value, source="webui")
        except (ValueError, CorrectionError, OSError):
            return error_response("invalid or ambiguous deterministic correction", status_code=400)
        return json_response({"date": diary_date, "correction": result})

    async def revisions(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            diary_date = _period("daily", self._query("date"))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"date": diary_date, "current_revision_id": self.storage.load_revision_state(diary_date).get("current_revision_id", ""), "revisions": list(self.storage.iter_revisions(diary_date) or ())})

    async def revision(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            diary_date = _period("daily", self._query("date")); revision_id = str(self._query("revision_id") or "")
            self.storage.validate_revision_id(revision_id)
            revision = self.storage.load_revision(diary_date, revision_id)
            if revision is None:
                raise ValueError("revision not found")
            markdown, metadata = self.storage.load_revision_contents(diary_date, revision_id)
        except (ValueError, OSError):
            return error_response("revision not found", status_code=404)
        return json_response({"revision": revision, "markdown": markdown, "metadata": metadata})

    async def revision_diff(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            diary_date = _period("daily", self._query("date")); left, right = str(self._query("from") or ""), str(self._query("to") or "")
            self.storage.validate_revision_id(left); self.storage.validate_revision_id(right)
            left_markdown, left_metadata = self.storage.load_revision_contents(diary_date, left)
            right_markdown, right_metadata = self.storage.load_revision_contents(diary_date, right)
        except (ValueError, OSError):
            return error_response("revision not found", status_code=404)
        return json_response({
            "date": diary_date, "from": left, "to": right,
            "markdown_changed": left_markdown != right_markdown,
            "metadata_changed": left_metadata != right_metadata,
            "before": {"markdown": left_markdown, "metadata": left_metadata},
            "after": {"markdown": right_markdown, "metadata": right_metadata},
        })

    async def rollback(self):
        if (denied := self._identity_error()) is not None:
            return denied
        payload = await self._payload()
        if payload is None:
            return error_response("JSON object required", status_code=400)
        try:
            diary_date = _period("daily", payload.get("date")); revision_id = str(payload.get("revision_id") or "")
            result = await self.corrections.rollback(diary_date, revision_id, source="webui")
        except (ValueError, CorrectionError, OSError):
            return error_response("invalid rollback target", status_code=400)
        return json_response({"date": diary_date, "correction": result})

    async def archives_list(self):
        if (denied := self._identity_error()) is not None:
            return denied
        rows = []
        for root, kind in ((self.archives.export_root, "export"), (self.archives.pre_restore_root, "pre_restore")):
            for path in sorted(root.glob("*.zip"), reverse=True) if root.exists() else ():
                if MAX_ARCHIVE_NAME.fullmatch(path.name) and not path.is_symlink() and path.is_file():
                    rows.append({"name": path.name, "kind": kind, "size": path.stat().st_size})
        return json_response({"archives": rows})

    async def archive_export(self):
        if (denied := self._identity_error()) is not None:
            return denied
        if await self._payload() is None:
            return error_response("JSON object required", status_code=400)
        try:
            path = await self.archives.export()
        except Exception:
            return error_response("archive export failed", status_code=500)
        return json_response({"archive": path.name})

    async def archive_verify(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            manifest = self.archives.verify(self._archive_path(self.storage, self._query("archive")))
        except (ValueError, ArchiveError):
            return error_response("archive verification failed", status_code=400)
        return json_response({"valid": True, "file_count": len(manifest.get("files", [])), "created_at": str(manifest.get("created_at") or ""), "plugin_version": str(manifest.get("plugin_version") or "")})

    async def archive_download(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            path = self._archive_path(self.storage, self._query("archive"))
            self.archives.verify(path)
        except (ValueError, ArchiveError):
            return error_response("archive is unavailable", status_code=404)
        return file_response(path, filename=path.name, content_type="application/zip")

    async def archive_restore(self):
        if (denied := self._identity_error()) is not None:
            return denied
        payload = await self._payload()
        if payload is None or not isinstance(payload.get("dry_run", False), bool):
            return error_response("JSON object and boolean dry_run required", status_code=400)
        try:
            result = await self.archives.restore(self._archive_path(self.storage, payload.get("archive")), dry_run=payload["dry_run"])
        except (ValueError, ArchiveError):
            return error_response("archive restore failed", status_code=400)
        return json_response(result)

    async def lifecycle(self):
        if (denied := self._identity_error()) is not None:
            return denied
        field, value = str(self._query("field") or ""), str(self._query("value") or "").strip()
        if field not in {"people", "projects"} or not value or len(value) > MAX_QUERY_LENGTH:
            return error_response("invalid lifecycle request", status_code=400)
        try:
            result = lifecycle(self.storage, value, field)
        except ValueError:
            return error_response("invalid lifecycle request", status_code=400)
        return json_response({"field": field, "value": value, "lifecycle": result})

    async def lifecycles(self):
        if (denied := self._identity_error()) is not None:
            return denied
        field = str(self._query("field") or "")
        if field not in {"people", "projects"}:
            return error_response("field must be people or projects", status_code=400)
        try:
            return json_response({"field": field, "entries": all_lifecycles(self.storage, field)})
        except ValueError:
            return error_response("invalid lifecycle request", status_code=400)

    async def reflections_list(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            kind, period = str(self._query("kind") or ""), _period(str(self._query("kind") or ""), self._query("period"))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if kind not in {"monthly", "yearly"}:
            return error_response("reflection kind must be monthly or yearly", status_code=400)
        metadata = self.storage.load_reflection_metadata(kind, period)
        if metadata is None:
            return json_response({"kind": kind, "period": period, "reflection": None})
        try:
            markdown = self.storage.reflection_path(kind, period).read_text(encoding="utf-8")
        except OSError:
            return error_response("reflection could not be read", status_code=500)
        return json_response({"kind": kind, "period": period, "markdown": markdown, "metadata": metadata})

    async def reflection_generate(self):
        if (denied := self._identity_error()) is not None:
            return denied
        payload = await self._payload()
        if payload is None or not isinstance(payload.get("force", False), bool):
            return error_response("JSON object and boolean force required", status_code=400)
        try:
            kind, period = str(payload.get("kind") or ""), _period(str(payload.get("kind") or ""), payload.get("period"))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if kind not in {"monthly", "yearly"}:
            return error_response("reflection kind must be monthly or yearly", status_code=400)
        provider_id = str(getattr(self.config, "generation_provider_id", "") or "")
        if not provider_id:
            return error_response("generation_provider_id is not configured", status_code=400)
        try:
            provider = self.context.get_provider_by_id(provider_id)
            if provider is None or not await self.reflections.generate(kind, period, provider, force=payload["force"]):
                raise RuntimeError
        except Exception:
            return error_response("reflection generation failed; inspect generation state", status_code=500)
        return json_response({"kind": kind, "period": period, "generated": True})

    async def integrity_check(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            return json_response(self.integrity.check())
        except Exception:
            return error_response("integrity check failed", status_code=500)

    async def integrity_repair(self):
        if (denied := self._identity_error()) is not None:
            return denied
        if await self._payload() is None:
            return error_response("JSON object required", status_code=400)
        try:
            return json_response(self.integrity.safe_repair())
        except Exception:
            return error_response("safe integrity repair failed", status_code=500)

    async def generate(self):
        if (denied := self._identity_error()) is not None:
            return denied
        try:
            payload = await request.json(default={}) if request is not None else {}
        except (TypeError, ValueError, UnicodeDecodeError):
            return error_response("JSON object required", status_code=400)
        if not isinstance(payload, dict):
            return error_response("JSON object required", status_code=400)
        try:
            kind, period = str(payload.get("kind") or ""), _period(str(payload.get("kind") or ""), payload.get("period"))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        force = payload.get("force", False)
        if not isinstance(force, bool):
            return error_response("force must be a boolean", status_code=400)
        provider_id = str(getattr(self.config, "generation_provider_id", "") or "")
        if not provider_id:
            return error_response("generation_provider_id is not configured", status_code=400)
        try:
            provider = self.context.get_provider_by_id(provider_id)
            if provider is None:
                raise RuntimeError("configured provider was not found")
            if kind == "daily":
                generate_unlocked = getattr(self.service, "_generate_unlocked", None)
                gate = getattr(self.service, "gate", None)
                if callable(generate_unlocked) and gate is not None:
                    # The daily pair and the derived review state must be one restore-safe unit.
                    async with gate.operation():
                        result = await generate_unlocked(date.fromisoformat(period), provider, force=force)
                        if diary_changed(result) and self.after_daily is not None:
                            await self.after_daily(date.fromisoformat(period), provider, "daily_rewritten" if force else "daily_added")
                else:
                    # Offline API stubs do not expose the internal orchestration hook.
                    result = await self.service.generate(date.fromisoformat(period), provider, force=force)
                    if diary_changed(result) and self.after_daily is not None:
                        await self.after_daily(date.fromisoformat(period), provider, "daily_rewritten" if force else "daily_added")
            else:
                result = await self.reviews.generate(kind, period, provider, force=force)
        except Exception:
            return error_response("generation failed; inspect generation state", status_code=500)
        if not result:
            return error_response("generation failed; inspect generation state", status_code=500)
        return json_response({"kind": kind, "period": period, "generated": True})


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result, seen = [], set()
    for item in value:
        text = str(item).strip() if isinstance(item, str) else ""
        if text and text.casefold() not in seen:
            result.append(text)
            seen.add(text.casefold())
    return result


def _limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}") from exc
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    return limit


def _required_text(value: Any, name: str) -> str:
    text = str(value or "")
    if not text or len(text) > MAX_QUERY_LENGTH:
        raise ValueError(f"{name} must contain 1 to {MAX_QUERY_LENGTH} characters")
    return text


def _reference(item: Any) -> dict[str, str]:
    return {"date": item.date, "title": item.title, "summary": item.summary}
