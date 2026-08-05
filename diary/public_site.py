"""Unauthenticated, read-only mobile diary reader."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from aiohttp import web

from .storage import DiaryStorage


DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MONTH_RE = re.compile(r"\d{4}-\d{2}\Z")


class DiaryPublicSite:
    def __init__(self, storage: DiaryStorage, assets_root: Path, port: int = 8788):
        self.storage = storage
        self.assets_root = Path(assets_root)
        self.port = port
        self.app = web.Application()
        self.app.add_routes((
            web.get("/", self.index),
            web.get("/app.js", self.asset),
            web.get("/styles.css", self.asset),
            web.get("/api/months", self.months),
            web.get("/api/entries", self.entries),
            web.get("/api/entries/{diary_date}", self.entry),
        ))
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = web.AppRunner(self.app, access_log=None)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        except Exception:
            await self._runner.cleanup()
            self._runner = None
            raise

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def index(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.assets_root / "index.html")

    async def asset(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.assets_root / request.path.lstrip("/"))

    def _dates(self) -> list[str]:
        root = self.storage.diary_root
        if not root.is_dir():
            return []
        return sorted((path.stem for path in root.glob("*.md") if DATE_RE.fullmatch(path.stem) and path.is_file()), reverse=True)

    @staticmethod
    def _date(value: str) -> str | None:
        if not DATE_RE.fullmatch(value):
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None

    def _reader_entry(self, diary_date: str, *, markdown: bool = False) -> dict[str, Any] | None:
        path = self.storage.diary_path(diary_date)
        if not path.is_file():
            return None
        metadata = self.storage.load_metadata(diary_date) or {}
        title = str(metadata.get("title") or "").strip()
        content = path.read_text(encoding="utf-8")
        if not title:
            title = next((line[2:].strip() for line in content.splitlines() if line.startswith("# ") and line[2:].strip()), diary_date)
        tags = metadata.get("tags")
        result = {
            "date": diary_date,
            "title": title,
            "mood": str(metadata.get("mood") or "").strip(),
            "tags": [str(tag).strip() for tag in tags if str(tag).strip()][:12] if isinstance(tags, list) else [],
        }
        if markdown:
            result["markdown"] = content
        return result

    async def months(self, _request: web.Request) -> web.Response:
        return web.json_response({"months": sorted({item[:7] for item in self._dates()}, reverse=True)})

    async def entries(self, request: web.Request) -> web.Response:
        month = str(request.query.get("month") or "")
        if not MONTH_RE.fullmatch(month):
            raise web.HTTPNotFound()
        try:
            date.fromisoformat(f"{month}-01")
        except ValueError:
            raise web.HTTPNotFound() from None
        return web.json_response({"entries": [entry for item in self._dates() if item.startswith(f"{month}-") if (entry := self._reader_entry(item))]})

    async def entry(self, request: web.Request) -> web.Response:
        diary_date = self._date(request.match_info["diary_date"])
        entry = self._reader_entry(diary_date, markdown=True) if diary_date else None
        if entry is None:
            raise web.HTTPNotFound()
        return web.json_response({"entry": entry})
