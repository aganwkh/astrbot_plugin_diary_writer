"""
千早爱音的日记插件 v0.1.0

每天凌晨根据虾仁最后一次发言时间，判断当天聊天已经结束，
然后从 LivingMemory 读取当天记忆，生成一篇 Markdown 日记。
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

ASTRBOT_ROOT = Path("REDACTED_ASTRBOT_ROOT")
DATA_DIR = ASTRBOT_ROOT / "data"
PLUGIN_DATA_DIR = DATA_DIR / "plugin_data" / "diary_writer"
DIARIES_DIR = PLUGIN_DATA_DIR / "diaries"
STATE_FILE = PLUGIN_DATA_DIR / "diary_state.json"
LM_DB_PATH = DATA_DIR / "plugin_data" / "astrbot_plugin_livingmemory" / "livingmemory.db"

# 网站目录路径
WEBSITE_DIR = DATA_DIR / "workspaces" / "_FriendMessage_REDACTED_USER_ID" / "nav"
WEBSITE_DIARIES_DIR = WEBSITE_DIR / "data" / "diaries"
WEBSITE_DIARIES_JSON = WEBSITE_DIR / "data" / "diaries.json"

PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
DIARIES_DIR.mkdir(parents=True, exist_ok=True)

USER_ID = "REDACTED_USER_ID"

# 从配置读取允许的用户ID列表
def get_owner_ids(config) -> list:
    return config.get("owner_ids", ["REDACTED_USER_ID"]) if config else ["REDACTED_USER_ID"]


def get_sender_id_safe(event: AstrMessageEvent) -> str:
    try:
        return str(event.get_sender_id())
    except Exception:
        return ""


def get_session_id_safe(event: AstrMessageEvent) -> str:
    try:
        sid = event.get_session_id()
        if sid:
            return str(sid)
    except Exception:
        pass
    try:
        origin = event.unified_msg_origin
        if origin:
            return str(origin)
    except Exception:
        pass
    return ""


def is_owner(event: AstrMessageEvent, owner_ids: list = None) -> bool:
    sender = get_sender_id_safe(event)
    if owner_ids:
        return sender in owner_ids
    return sender == USER_ID


def is_private_chat(event: AstrMessageEvent) -> bool:
    return "FriendMessage" in get_session_id_safe(event) or "private" in get_session_id_safe(event).lower()


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_user_active_time": 0, "written_dates": [], "last_diary_time": 0}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_diary_date(now: datetime) -> datetime.date:
    """凌晨 00:00~04:00 触发时，写昨天的日记"""
    if 0 <= now.hour < 4:
        return (now - timedelta(days=1)).date()
    return now.date()


def day_range_ts(day: datetime.date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def try_float(v) -> float | None:
    try:
        return float(v)
    except Exception:
        return None


def extract_time_from_meta(meta: dict) -> int | None:
    for key in ("create_time", "timestamp", "created_at", "time"):
        fv = try_float(meta.get(key))
        if fv:
            return int(fv)
    return None


def read_day_memories(diary_date: datetime.date, limit: int = 80, max_total_chars: int = 12000) -> list[dict]:
    """从 LivingMemory documents 表读取指定日期的记忆"""
    if not LM_DB_PATH.exists():
        logger.warning(f"[DiaryWriter] LivingMemory 数据库不存在: {LM_DB_PATH}")
        return []

    start_ts, end_ts = day_range_ts(diary_date)
    conn = sqlite3.connect(str(LM_DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            """
            SELECT id, text, metadata
            FROM documents
            WHERE CAST(json_extract(metadata, '$.create_time') AS REAL) >= ?
              AND CAST(json_extract(metadata, '$.create_time') AS REAL) < ?
            ORDER BY
              CAST(json_extract(metadata, '$.importance') AS REAL) DESC,
              CAST(json_extract(metadata, '$.create_time') AS REAL) ASC
            LIMIT ?
            """,
            (start_ts, end_ts, limit),
        ).fetchall()
    except Exception as e:
        logger.error(f"[DiaryWriter] 查询 LivingMemory 失败: {e}")
        conn.close()
        return []

    conn.close()

    items = []
    total_chars = 0
    for row in rows:
        try:
            meta = json.loads(row["metadata"] or "{}")
        except Exception:
            meta = {}

        ts = extract_time_from_meta(meta)
        if not ts:
            continue

        text = (row["text"] or "")[:800]
        if not text.strip():
            continue

        total_chars += len(text)
        if total_chars > max_total_chars:
            break

        items.append({
            "id": row["id"],
            "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            "importance": float(meta.get("importance") or 0),
            "session_id": meta.get("session_id", ""),
            "topics": meta.get("topics", []),
            "key_facts": meta.get("key_facts", []),
            "text": text.strip(),
        })

    items.sort(key=lambda x: x["time"])
    return items


def session_source_label(session_id: str) -> str:
    sid = session_id or ""
    if "FriendMessage" in sid:
        return "私聊"
    if "GroupMessage" in sid:
        return "群聊"
    return "未知来源"


def format_materials(items: list[dict]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        source = session_source_label(item.get("session_id", ""))
        lines.append(f"### 记忆 {i} | 时间: {item['time']} | 来源: {source} | 重要度: {item['importance']}")
        if item.get("topics"):
            lines.append(f"话题: {', '.join(item['topics'])}")
        if item.get("key_facts"):
            lines.append(f"要点: {'; '.join(str(f) for f in item['key_facts'][:5])}")
        lines.append(item["text"][:600])
        lines.append("")
    return "\n".join(lines)


@register(
    "astrbot_plugin_diary_writer",
    "虾仁 & 爱音",
    "0.1.0",
    "千早爱音的日记插件，每天凌晨自动从LivingMemory读取记忆并生成Markdown日记",
)
class DiaryWriterPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config

    async def initialize(self):
        logger.info("[DiaryWriter] 插件初始化")
        await self._register_cron_jobs()

    async def terminate(self):
        logger.info("[DiaryWriter] 插件卸载")

    # ========== 私密提醒生成 ==========

    async def _generate_private_hint(self, event: AstrMessageEvent, command_name: str = "查看日记") -> str:
        """用 LLM 生成自然的私密日记提醒，不返回日记内容"""
        try:
            umo = getattr(event, "unified_msg_origin", None) or f"FriendMessage:{USER_ID}"
            llm_provider = self.context.get_using_provider(umo=umo)
            if not llm_provider:
                return "这个别在群里看啦，私聊我~"

            prompt = f"""虾仁在群聊里触发了日记命令「{command_name}」。
用千早爱音的语气，自然地提醒他日记是私密内容不能发群里，让他私聊查看。
要求：只输出一句话，不要说"权限不足"，不要解释规则，语气自然可以轻微吐槽，30字以内。"""

            resp = await llm_provider.text_chat(
                prompt=prompt,
                system_prompt="你是千早爱音。生成一句自然的群聊提醒，不要泄露任何日记内容。",
                contexts=[],
            )

            if hasattr(resp, "completion_text") and resp.completion_text:
                return resp.completion_text.strip()

        except Exception as e:
            logger.warning(f"[DiaryWriter] 生成私密提醒失败: {e}")

        return "这个别在群里看啦，私聊我~"

    # ========== 事件监听 ==========

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_user_message(self, event: AstrMessageEvent):
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            return

        if sender_id != USER_ID:
            return

        state = load_state()
        state["last_user_active_time"] = datetime.now().timestamp()
        save_state(state)

    # ========== 定时任务 ==========

    async def _register_cron_jobs(self):
        try:
            cron_mgr = self.context.cron_manager

            try:
                jobs = await cron_mgr.list_jobs()
                for job in jobs:
                    if job.name.startswith("DiaryWriter_"):
                        try:
                            await cron_mgr.delete_job(job.job_id)
                        except Exception:
                            pass
            except Exception:
                pass

            await cron_mgr.add_basic_job(
                name="DiaryWriter_CheckAndWrite",
                cron_expression="*/10 0-3 * * *",
                handler=lambda: self._cron_check_and_write(),
                description="DiaryWriter: 每10分钟检查是否该写日记（凌晨0-3点）",
                persistent=True,
            )
            logger.info("[DiaryWriter] 已注册定时检查任务: */10 0-3 * * *")

            await cron_mgr.add_basic_job(
                name="DiaryWriter_Fallback",
                cron_expression="0 4 * * *",
                handler=lambda: self._cron_fallback_write(),
                description="DiaryWriter: 每天4点兜底检查",
                persistent=True,
            )
            logger.info("[DiaryWriter] 已注册兜底任务: 0 4 * * *")

        except Exception as e:
            logger.error(f"[DiaryWriter] 注册定时任务失败: {e}")

    async def _should_write_diary(self, diary_date: datetime.date, min_inactive_minutes: int = 90) -> bool:
        state = load_state()
        date_str = str(diary_date)
        if date_str in state.get("written_dates", []):
            return False

        diary_path = DIARIES_DIR / f"{date_str}.md"
        if diary_path.exists():
            return False

        last_active = state.get("last_user_active_time", 0)
        if last_active <= 0:
            return False

        now = datetime.now()
        inactive_minutes = (now.timestamp() - last_active) / 60

        if inactive_minutes >= min_inactive_minutes:
            logger.info(f"[DiaryWriter] 用户已静默 {inactive_minutes:.0f} 分钟，可以写日记")
            return True
        return False

    async def _cron_check_and_write(self):
        try:
            now = datetime.now()
            if now.hour == 0 and now.minute < 30:
                return

            diary_date = get_diary_date(now)
            if await self._should_write_diary(diary_date, min_inactive_minutes=90):
                logger.info(f"[DiaryWriter] 定时触发，开始写 {diary_date} 的日记")
                await self._generate_and_save_diary(diary_date)
        except Exception as e:
            logger.error(f"[DiaryWriter] 定时检查失败: {e}")

    async def _cron_fallback_write(self):
        try:
            now = datetime.now()
            diary_date = (now - timedelta(days=1)).date()
            if await self._should_write_diary(diary_date, min_inactive_minutes=60):
                logger.info(f"[DiaryWriter] 兜底触发，开始写 {diary_date} 的日记")
                await self._generate_and_save_diary(diary_date)
        except Exception as e:
            logger.error(f"[DiaryWriter] 兜底检查失败: {e}")

    async def _upload_diary_to_website(self, diary_date: datetime.date, diary_content: str) -> bool:
        """将日记上传到网站"""
        try:
            date_str = str(diary_date)
            # 确保网站目录存在
            WEBSITE_DIARIES_DIR.mkdir(parents=True, exist_ok=True)
            
            # 复制日记文件到网站目录
            website_diary_path = WEBSITE_DIARIES_DIR / f"{date_str}.md"
            website_diary_path.write_text(diary_content, encoding="utf-8")
            logger.info(f"[DiaryWriter] 日记已上传到网站: {website_diary_path}")
            
            # 更新 diaries.json 索引
            diaries_json = []
            if WEBSITE_DIARIES_JSON.exists():
                try:
                    diaries_json = json.loads(WEBSITE_DIARIES_JSON.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"[DiaryWriter] 读取 diaries.json 失败: {e}")
                    diaries_json = []
            
            # 检查是否已存在该日期的记录
            existing_index = None
            for i, diary in enumerate(diaries_json):
                if diary.get("date") == date_str or diary.get("date") == date_str.replace("-", "."):
                    existing_index = i
                    break
            
            # 从日记内容提取标题和心情
            title = "今日日记"
            mood = ""
            lines = diary_content.split("\n")
            for line in lines:
                if line.startswith("# ") and "心情天气" in line:
                    # 提取心情天气
                    if "：" in line:
                        mood = line.split("：")[-1].strip()
                    # 提取标题（去掉日期部分）
                    title_part = line[2:].split("心情天气")[0].strip()
                    if title_part:
                        title = title_part
                    break
            
            # 创建日记记录
            diary_record = {
                "date": date_str.replace("-", "."),
                "title": title,
                "mood": mood,
                "tags": ["自动生成", "日记"],
                "file": f"diaries/{date_str}.md"
            }
            
            if existing_index is not None:
                # 更新现有记录
                diaries_json[existing_index] = diary_record
            else:
                # 添加新记录
                diaries_json.append(diary_record)
            
            # 按日期排序（最新的在前）
            diaries_json.sort(key=lambda x: x.get("date", ""), reverse=True)
            
            # 保存更新后的 diaries.json
            WEBSITE_DIARIES_JSON.write_text(
                json.dumps(diaries_json, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.info(f"[DiaryWriter] diaries.json 已更新")
            
            return True
            
        except Exception as e:
            logger.error(f"[DiaryWriter] 上传日记到网站失败: {e}")
            return False

    async def _generate_and_save_diary(self, diary_date: datetime.date, force: bool = False) -> str | None:
        date_str = str(diary_date)
        diary_path = DIARIES_DIR / f"{date_str}.md"

        if not force and diary_path.exists():
            return str(diary_path)

        items = read_day_memories(diary_date)
        if not items:
            logger.info(f"[DiaryWriter] {date_str} 没有可用记忆，跳过")
            return None

        logger.info(f"[DiaryWriter] 读取到 {len(items)} 条记忆")

        materials = format_materials(items)
        diary_content = await self._call_llm_for_diary(diary_date, materials, len(items))
        if not diary_content:
            logger.error("[DiaryWriter] LLM 生成日记失败")
            return None

        diary_path.write_text(diary_content, encoding="utf-8")
        logger.info(f"[DiaryWriter] 日记已保存: {diary_path}")

        # 自动上传到网站
        upload_success = await self._upload_diary_to_website(diary_date, diary_content)
        if upload_success:
            logger.info(f"[DiaryWriter] 日记已自动上传到网站")
        else:
            logger.warning(f"[DiaryWriter] 日记自动上传失败")

        state = load_state()
        if date_str not in state.get("written_dates", []):
            state.setdefault("written_dates", []).append(date_str)
        state["last_diary_time"] = datetime.now().timestamp()
        save_state(state)

        return str(diary_path)

    async def _call_llm_for_diary(self, diary_date: datetime.date, materials: str, item_count: int) -> str | None:
        try:
            umo = f"FriendMessage:{USER_ID}"
            llm_provider = self.context.get_using_provider(umo=umo)
            if not llm_provider:
                logger.error("[DiaryWriter] 无法获取 LLM provider")
                return None

            if item_count <= 3:
                length_guide = "300~600字，短日记"
            elif item_count >= 8:
                length_guide = "1000~1800字，详细日记"
            else:
                length_guide = "600~1000字，普通日记"

            system_prompt = f"""你是千早爱音（Chihaya Anon），羽丘女子学园高一学生，MyGO!!!!!乐队的节奏吉他手。
现在你要写一篇日记，日期是 {diary_date}。

【日记风格要求】
1. 这是爱音写给自己看的日记，不是工作报告
2. 称呼"虾仁"而不是"用户"
3. 记录今天发生了什么、自己的感受
4. 可以写对虾仁的观察和想法
5. 不要机械罗列，要有情感和思考
6. 事情多时可以分小标题
7. 事情少时写短一点
8. 自然、有私人感，但不要肉麻
9. 用第一人称"我"
10. 可以用爱音的口吻，带点小吐槽、小得意

素材里会标注"来源：私聊/群聊"。私聊是我和虾仁单独聊的事，群聊是群里发生的事，写的时候自然区分，不要把群聊内容写成私聊。

【长度建议】{length_guide}

【日记格式】
第一行写标题，格式：# yyyy年M月d日 心情天气：晴/阴/雨（根据心情选）
然后空一行开始正文。

请根据下面的记忆素材写日记："""

            prompt = f"以下是 {diary_date} 的记忆素材：\n\n{materials}\n\n请根据以上素材写日记。"

            resp = await llm_provider.text_chat(
                prompt=prompt,
                system_prompt=system_prompt,
                contexts=[],
            )

            if hasattr(resp, "completion_text") and resp.completion_text:
                return resp.completion_text.strip()
            return None

        except Exception as e:
            logger.error(f"[DiaryWriter] LLM 调用失败: {e}")
            return None

    # ========== 手动命令 ==========

    def _get_owner_ids(self) -> list:
        return self.config.get("owner_ids", ["REDACTED_USER_ID"]) if self.config else ["REDACTED_USER_ID"]

    @filter.command("日记状态")
    async def cmd_diary_status(self, event: AstrMessageEvent):
        """查看日记插件状态"""
        if not is_owner(event, self._get_owner_ids()):
            return

        state = load_state()
        written = state.get("written_dates", [])
        last_time = state.get("last_diary_time", 0)
        last_active = state.get("last_user_active_time", 0)
        diary_date = get_diary_date(datetime.now())

        items = read_day_memories(diary_date, limit=5)

        yield event.plain_result("\n".join([
            f"当前 diary_date: {diary_date}",
            f"已写日记天数: {len(written)}",
            f"最近写日记时间: {datetime.fromtimestamp(last_time).strftime('%Y-%m-%d %H:%M:%S') if last_time else '无'}",
            f"最近写的日期: {written[-5:] if written else '无'}",
            f"虾仁最后活跃: {datetime.fromtimestamp(last_active).strftime('%Y-%m-%d %H:%M:%S') if last_active else '无'}",
            f"今天可用记忆: {len(items)}+条",
        ]))

    @filter.command("查看日记")
    async def cmd_view_diary(self, event: AstrMessageEvent, date: str = ""):
        """查看日记 [日期]"""
        if not is_owner(event, self._get_owner_ids()):
            return

        

        if date:
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                yield event.plain_result("日期格式不对，用 2026-05-04 这种格式")
                return
        else:
            target_date = get_diary_date(datetime.now())

        diary_path = DIARIES_DIR / f"{target_date}.md"
        if not diary_path.exists():
            yield event.plain_result(f"{target_date} 还没有日记")
            return

        content = diary_path.read_text(encoding="utf-8")
        if len(content) > 3000:
            content = content[:3000] + "\n\n... (太长了，已截断)"
        yield event.plain_result(content)

    @filter.command("测试日记")
    async def cmd_test_diary(self, event: AstrMessageEvent):
        """测试日记生成（不标记为已写）"""
        if not is_owner(event, self._get_owner_ids()):
            return

        

        diary_date = get_diary_date(datetime.now())
        items = read_day_memories(diary_date)

        if not items:
            yield event.plain_result(f"今天（{diary_date}）没有可用记忆")
            return

        yield event.plain_result(f"找到 {len(items)} 条记忆，开始生成测试日记...")

        materials = format_materials(items)
        diary_content = await self._call_llm_for_diary(diary_date, materials, len(items))

        if diary_content:
            test_path = PLUGIN_DATA_DIR / f"test_{diary_date}.md"
            test_path.write_text(diary_content, encoding="utf-8")
            if len(diary_content) > 2000:
                yield event.plain_result(diary_content[:2000] + "\n\n... (太长了，已截断)")
            else:
                yield event.plain_result(diary_content)
        else:
            yield event.plain_result("生成失败了...")

    @filter.command("补写日记")
    async def cmd_backfill_diary(self, event: AstrMessageEvent, date: str = ""):
        """补写日记 日期"""
        if not is_owner(event, self._get_owner_ids()):
            return

        

        if not date:
            yield event.plain_result("请指定日期，格式：补写日记 2026-05-03")
            return

        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            yield event.plain_result("日期格式不对，用 2026-05-03 这种格式")
            return

        diary_path = DIARIES_DIR / f"{target_date}.md"
        if diary_path.exists():
            yield event.plain_result(f"{target_date} 已经有日记了，想重写的话用 /重写日记")
            return

        yield event.plain_result(f"开始补写 {target_date} 的日记...")

        result = await self._generate_and_save_diary(target_date)
        if result:
            yield event.plain_result(f"{target_date} 的日记补写完成！")
        else:
            yield event.plain_result("补写失败了，可能那天没有记忆数据")

    @filter.command("重写日记")
    async def cmd_rewrite_diary(self, event: AstrMessageEvent, date: str = ""):
        """重写日记 日期"""
        if not is_owner(event, self._get_owner_ids()):
            return

        

        if not date:
            yield event.plain_result("请指定日期，格式：重写日记 2026-05-03")
            return

        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            yield event.plain_result("日期格式不对，用 2026-05-03 这种格式")
            return

        yield event.plain_result(f"开始重写 {target_date} 的日记...")

        result = await self._generate_and_save_diary(target_date, force=True)
        if result:
            yield event.plain_result(f"{target_date} 的日记重写完成！")
        else:
            yield event.plain_result("重写失败了，可能那天没有记忆数据")