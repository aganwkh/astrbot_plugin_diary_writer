import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


PLUGIN_ROOT = Path(__file__).resolve().parent
PLUGIN_DATA_ROOT = PLUGIN_ROOT / "data" / "diary_writer"
PUBLIC_SITE_ASSETS = PLUGIN_ROOT / "pages" / "diary-reader"

if __package__:
    # AstrBot imports plugins below ``data.plugins``.  Relative imports keep the
    # bundled diary package reachable without adding the plugin root to sys.path.
    from .diary.activity import DailyActivityTracker
    from .diary.ask_diary import ask_diary
    from .diary.config import DiaryConfig
    from .diary.corrections import CorrectionError, CorrectionService
    from .diary.maintenance import GLOBAL_MAINTENANCE_GATE
    from .diary.memory_source import SQLiteLivingMemorySource
    from .diary.migration import migrate_legacy_directory, migrate_legacy_markdown, migrate_plugin_data_directory
    from .diary.models import GenerationState
    from .diary.permissions import (
        can_access_sensitive_diary,
        can_use_group_command,
        is_authorized,
        private_only_reminder,
    )
    from .diary.retrieval import on_this_day, timeline
    from .diary.schedule import should_generate, should_run_regular_check
    from .diary.service import DiaryService
    from .diary.storage import DiaryStorage
    from .diary.public_site import DiaryPublicSite
    from .diary.web_api import DiaryWebApi
else:
    # The offline smoke suite loads main.py as a standalone module.
    from diary.activity import DailyActivityTracker
    from diary.ask_diary import ask_diary
    from diary.config import DiaryConfig
    from diary.corrections import CorrectionError, CorrectionService
    from diary.maintenance import GLOBAL_MAINTENANCE_GATE
    from diary.memory_source import SQLiteLivingMemorySource
    from diary.migration import migrate_legacy_directory, migrate_legacy_markdown, migrate_plugin_data_directory
    from diary.models import GenerationState
    from diary.permissions import (
        can_access_sensitive_diary,
        can_use_group_command,
        is_authorized,
        private_only_reminder,
    )
    from diary.retrieval import on_this_day, timeline
    from diary.schedule import should_generate, should_run_regular_check
    from diary.service import DiaryService
    from diary.storage import DiaryStorage
    from diary.public_site import DiaryPublicSite
    from diary.web_api import DiaryWebApi


def _message_text(event) -> str:
    try:
        value = event.get_message_str()
    except Exception:
        value = getattr(event, "message_str", "")
    return str(value or "").lstrip()


@register("astrbot_plugin_diary_writer", "aganwkh", "1.1.3", "私密、可追溯的长期 AI 日记")
class DiaryWriterPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = DiaryConfig.from_mapping(config)
        data_root = Path(get_astrbot_data_path())
        root = PLUGIN_DATA_ROOT
        self.legacy_plugin_root = data_root / "plugin_data" / "astrbot_plugin_diary_writer"
        self.legacy_diary_root = data_root / "plugin_data" / "diary_writer" / "diaries"
        self.storage = DiaryStorage(root)
        self.service = DiaryService(
            self.config,
            self.storage,
            SQLiteLivingMemorySource(self.config.livingmemory_path(Path(get_astrbot_data_path()))),
            persona_resolver=self._astrbot_persona_prompt,
        )
        self.corrections = CorrectionService(self.storage)
        self.activity_tracker = DailyActivityTracker(self.storage, saved_rounds=2)
        self.web_api = DiaryWebApi(
            context, self.config, self.storage, self.service, self._astrbot_persona_prompt,
        )
        self.web_api.register()
        self.public_site = DiaryPublicSite(self.storage, PUBLIC_SITE_ASSETS, self.config.public_site_port)
        self._reminder_lock = asyncio.Lock()

    async def initialize(self):
        async with GLOBAL_MAINTENANCE_GATE.operation():
            migrated_plugin_data = migrate_plugin_data_directory(self.legacy_plugin_root, self.storage)
            migrated = migrate_legacy_directory(self.legacy_diary_root, self.storage)
            if migrated_plugin_data:
                logger.info("[DiaryWriter] copied existing plugin data into the plugin directory")
            if migrated:
                logger.info(f"[DiaryWriter] migrated {migrated} v0.2 diary files")
        try:
            await self.public_site.start()
        except OSError as exc:
            logger.warning(f"[DiaryWriter] public diary site could not start: {exc}")
        try:
            jobs = await self.context.cron_manager.list_jobs()
            for job in jobs:
                if job.name.startswith("DiaryWriter_"):
                    await self.context.cron_manager.delete_job(job.job_id)
        except Exception as exc:
            logger.warning(f"[DiaryWriter] could not clear previous cron jobs: {exc}")
        if not self.config.can_auto_write:
            return
        state = self.storage.load_daily_finalization_state()
        if not state.get("effective_date"):
            self.storage.save_daily_finalization_state({"effective_date": datetime.now().date().isoformat(), "initialized_at": datetime.now().astimezone().isoformat()})
        try:
            await self.context.cron_manager.add_basic_job(name="DiaryWriter_CheckAndWrite", cron_expression="*/10 0-3 * * *", handler=self._cron, description="DiaryWriter automatic generation", persistent=True)
            await self.context.cron_manager.add_basic_job(name="DiaryWriter_Fallback", cron_expression="0 4 * * *", handler=self._fallback, description="DiaryWriter fallback generation", persistent=True)
            await self.context.cron_manager.add_basic_job(name="DiaryWriter_Finalization", cron_expression="10 4 * * *", handler=self._daily_finalization, description="DiaryWriter daily finalization", persistent=True)
        except Exception as exc:
            logger.error(f"[DiaryWriter] could not register cron jobs: {exc}")
        await self._daily_finalization()

    async def terminate(self):
        await self.public_site.stop()

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_user_message(self, event: AstrMessageEvent):
        text = _message_text(event)
        if self._private(event) and text and not text.startswith("/"):
            async with GLOBAL_MAINTENANCE_GATE.operation():
                self.storage.save_activity(datetime.now().astimezone().isoformat())
                await self.activity_tracker.record(datetime.now().date(), datetime.now().astimezone().isoformat(), text, str(getattr(event, "unified_msg_origin", "") or ""))

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_this_day_reminder(self, event: AstrMessageEvent):
        """A best-effort private reminder; it never stops the triggering message."""
        if not self.config.on_this_day_reminder_enabled or not self._private(event) or _message_text(event).startswith("/"):
            return
        async with self._reminder_lock:
            async with GLOBAL_MAINTENANCE_GATE.operation():
                today = datetime.now().date()
                if self.storage.load_reminder_state().get("date") == today.isoformat():
                    return
                matches = on_this_day(self.storage, today)
                if not matches:
                    return
                self.storage.save_reminder_state({
                    "date": today.isoformat(),
                    "recipient_id": str(event.get_sender_id()),
                    "reminded_at": datetime.now().astimezone().isoformat(),
                })
                entries = "\n".join(f"{item.date}：{item.title} — {item.summary}" for item in matches[:3])
            yield event.plain_result(f"去年的今天，我们记录过：\n{entries}")

    async def _provider(self, event=None):
        if self.config.generation_provider_id:
            return self.context.get_provider_by_id(self.config.generation_provider_id)
        if event is not None:
            return self.context.get_using_provider(umo=event.unified_msg_origin)
        return None

    async def _astrbot_persona_prompt(self, session_ids: list[str]) -> str:
        """Resolve AstrBot's effective persona without copying or overriding it in plugin config."""
        manager = getattr(self.context, "persona_manager", None)
        conversation_manager = getattr(self.context, "conversation_manager", None)
        if manager is None or conversation_manager is None:
            return ""
        for umo in reversed(list(dict.fromkeys(str(item) for item in session_ids if str(item)))):
            conversation_id = await conversation_manager.get_curr_conversation_id(umo)
            conversation = await conversation_manager.get_conversation(umo, conversation_id) if conversation_id else None
            config = self.context.get_config(umo=umo)
            provider_settings = config.get("provider_settings", {})
            conversation_persona_id = getattr(conversation, "persona_id", None)
            persona_id, persona, forced_persona_id, use_webchat_default = await manager.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=conversation_persona_id,
                platform_name=umo.split(":", 1)[0],
                provider_settings=provider_settings,
            )
            if persona_id == "[%None]" or use_webchat_default:
                return ""
            if persona and str(persona.get("prompt") or "").strip():
                return str(persona["prompt"]).strip()
            if not forced_persona_id and conversation_persona_id is None:
                default_persona = await manager.get_default_persona_v3(umo)
                if default_persona and str(default_persona.get("prompt") or "").strip():
                    return str(default_persona["prompt"]).strip()
            if persona_id:
                return ""
        default_persona = await manager.get_default_persona_v3()
        return str(default_persona.get("prompt") or "").strip() if default_persona else ""

    async def _cron(self):
        if not should_run_regular_check(datetime.now(), self.config.cron_start_delay_minutes):
            return
        await self._automatic(self.config.inactive_minutes)

    async def _fallback(self):
        await self._automatic(self.config.fallback_inactive_minutes)

    async def _daily_finalization(self):
        """At 04:10, fill only post-v1.1 gaps; no missing-material path is a skip."""
        if not self.config.can_auto_write:
            return
        now = datetime.now()
        yesterday = (now - timedelta(days=1)).date()
        state = self.storage.load_daily_finalization_state()
        try:
            effective = datetime.strptime(str(state.get("effective_date") or yesterday.isoformat()), "%Y-%m-%d").date()
        except ValueError:
            effective = now.date()
            state["effective_date"] = effective.isoformat()
        if yesterday < effective:
            state.update({"effective_date": effective.isoformat(), "last_finalization_at": now.astimezone().isoformat(), "last_checked_through": yesterday.isoformat()})
            self.storage.save_daily_finalization_state(state)
            return
        provider = await self._provider()
        async with GLOBAL_MAINTENANCE_GATE.operation():
            target = effective
            while target <= yesterday:
                if self.storage.has_any_diary(target.isoformat()):
                    migrate_legacy_markdown(self.storage, target.isoformat())
                else:
                    if provider is None:
                        self.storage.save_generation_state(GenerationState(
                            pending_date=target.isoformat(), stage="failed",
                            last_error="generation provider unavailable", updated_at=now.astimezone().isoformat(),
                        ))
                        return
                    result = await self.service._generate_unlocked(target, provider)
                    if not result:
                        break
                target += timedelta(days=1)
            state.update({"effective_date": effective.isoformat(), "last_finalization_at": now.astimezone().isoformat(), "last_checked_through": yesterday.isoformat()})
            self.storage.save_daily_finalization_state(state)

    async def _automatic(self, inactive_minutes):
        now = datetime.now(); raw = self.storage.load_activity()
        try: active = datetime.fromisoformat(raw)
        except ValueError: return
        if not should_generate(now, active, inactive_minutes): return
        provider = await self._provider()
        if not provider: return
        target = (now - timedelta(days=1)).date()
        async with GLOBAL_MAINTENANCE_GATE.operation():
            if self.storage.has_any_diary(target.isoformat()):
                migrate_legacy_markdown(self.storage, target.isoformat())
            else:
                result = await self.service._generate_unlocked(target, provider)

    def _private(self, event): return can_access_sensitive_diary(event, self.config)

    @filter.command("日记状态")
    async def status(self, event: AstrMessageEvent):
        if not is_authorized(event, self.config): return
        if not self._private(event) and not can_use_group_command(event, self.config, "日记状态"): return
        yield event.plain_result(f"自动写作：{'开启' if self.config.can_auto_write else '关闭'}\n已生成日记：{len(list(self.storage.diary_root.glob('*.md')))}")

    @filter.command("查看日记")
    async def view(self, event: AstrMessageEvent, date: str = ""):
        if not self._private(event):
            if is_authorized(event, self.config): yield event.plain_result(private_only_reminder())
            return
        target = date or (datetime.now() - timedelta(days=1)).date().isoformat(); path = self.storage.diary_path(target)
        yield event.plain_result(path.read_text(encoding="utf-8")[:3000] if path.exists() else f"{target} 还没有日记")

    async def _write(self, event: AstrMessageEvent, date: str, force: bool):
        if not self._private(event):
            if is_authorized(event, self.config): yield event.plain_result(private_only_reminder())
            return
        try: target = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError: yield event.plain_result("请使用 YYYY-MM-DD 日期"); return
        provider = await self._provider(event)
        async with GLOBAL_MAINTENANCE_GATE.operation():
            result = await self.service._generate_unlocked(
                target, provider, force=force,
                persona_session_id=str(getattr(event, "unified_msg_origin", "") or ""),
            )
        yield event.plain_result("日记已保存（重写已备份旧版本）。" if result else "生成失败；请查看 generation_state.json。")

    @filter.command("补写日记")
    async def backfill(self, event: AstrMessageEvent, date: str = ""):
        async for result in self._write(event, date, False): yield result

    @filter.command("重写日记")
    async def rewrite(self, event: AstrMessageEvent, date: str = ""):
        async for result in self._write(event, date, True): yield result

    @filter.command("测试日记")
    async def preview(self, event: AstrMessageEvent):
        if not self._private(event):
            if is_authorized(event, self.config): yield event.plain_result(private_only_reminder())
            return
        draft = await self.service.preview(
            datetime.now().date(), await self._provider(event),
            persona_session_id=str(getattr(event, "unified_msg_origin", "") or ""),
        )
        yield event.plain_result(draft[:3000] if draft else "预览生成失败；未写入任何日记文件。")

    @filter.command("纠正日记")
    async def correct_diary(self, event: AstrMessageEvent, instruction: str = ""):
        """Apply only an exact field replacement; this path never calls an LLM."""
        if not self._private(event):
            if is_authorized(event, self.config): yield event.plain_result(private_only_reminder())
            return
        match = re.fullmatch(r'\s*(\d{4}-\d{2}-\d{2})\s+([a-z_]+)\s+"([^"\r\n]+)"\s*(?:->|→)\s*"([^"\r\n]+)"\s*', instruction or "")
        if not match:
            yield event.plain_result('格式：/纠正日记 YYYY-MM-DD field "旧值" -> "新值"')
            return
        diary_date, field, old_value, new_value = match.groups()
        try:
            await self.corrections.replace(diary_date, field, old_value, new_value, source="command")
        except (CorrectionError, ValueError):
            yield event.plain_result("未执行纠错：目标必须是唯一的当前精确值。")
            return
        yield event.plain_result("纠错已保存；旧版本和更正记录均可回滚追溯。")

    @filter.command("问日记")
    async def ask(self, event: AstrMessageEvent, question: str = ""):
        if not self._private(event):
            if is_authorized(event, self.config): yield event.plain_result(private_only_reminder())
            return
        yield event.plain_result((await ask_diary(self.storage, question, await self._provider(event)))[:3000])

    @filter.command("那年今日")
    async def on_this_day(self, event: AstrMessageEvent):
        if not self._private(event):
            if is_authorized(event, self.config): yield event.plain_result(private_only_reminder())
            return
        matches = on_this_day(self.storage, datetime.now().date())
        yield event.plain_result("\n".join(f"{item.date}｜{item.title}：{item.summary}" for item in matches) or "没有往年同日的日记。")

    async def _timeline(self, event, value, field):
        if not self._private(event):
            if is_authorized(event, self.config): yield event.plain_result(private_only_reminder())
            return
        result = timeline(self.storage, value, field)
        if result is None:
            yield event.plain_result("未检索到相关日记。"); return
        related = "\n".join(f"{item.date}｜{item.summary}" for item in result.entries)
        yield event.plain_result(f"首次：{result.first.date}｜{result.first.summary}\n最近：{result.latest.date}｜{result.latest.summary}\n相关记录：\n{related}")

    @filter.command("日记项目")
    async def project(self, event: AstrMessageEvent, value: str = ""):
        async for result in self._timeline(event, value, "projects"): yield result

    @filter.command("日记话题")
    async def topic(self, event: AstrMessageEvent, value: str = ""):
        async for result in self._timeline(event, value, "topics"): yield result
