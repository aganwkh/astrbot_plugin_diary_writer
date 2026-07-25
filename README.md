# AI 日记作家

基于 LivingMemory 的私密、可追溯长期 AI 日记插件。v0.3.0 同时保存 Markdown 与结构化 metadata，并把日记主要事件关联到实际的 LivingMemory memory ID。

## 命令与权限

只有 `owner_ids` 中的用户可用。日记正文、测试、补写和重写只接受私聊；群聊最多允许配置中的无敏感 `/日记状态`。

| 命令 | 说明 |
| --- | --- |
| `/日记状态` | 显示非敏感运行统计 |
| `/查看日记 [YYYY-MM-DD]` | 查看私聊日记正文 |
| `/测试日记` | 只生成预览草稿，不写任何日记、状态或连续性数据 |
| `/补写日记 YYYY-MM-DD` | 为没有日记的日期生成日记 |
| `/重写日记 YYYY-MM-DD` | 重写并保留带时间戳的旧版本备份 |

## 自动生成

- 00:00–03:59 每十分钟检查一次；默认 00:30 后才开始检查。
- 仅当授权用户已静默至少 90 分钟才生成前一天日记。
- 04:00 有独立兜底检查，默认静默阈值为 60 分钟。
- `auto_write_enabled: false` 时不注册自动任务。

可在 `_conf_schema.json` 对应的 AstrBot 插件配置中调整 `inactive_minutes`、`fallback_inactive_minutes` 和 `cron_start_delay_minutes`。

## 数据与兼容性

v0.3 数据在 AstrBot 标准数据目录的 `plugin_data/astrbot_plugin_diary_writer/`：

- `diaries/YYYY-MM-DD.md`：供人阅读的日记。
- `metadata/YYYY-MM-DD.json`：事件、证据 memory IDs、主题、心情和生成信息。
- `continuity.json` 与 `generation_state.json`：连续性和可恢复生成状态。
- `backups/`：重写前的 Markdown/metadata 备份。

初始化时会从旧版 `plugin_data/diary_writer/diaries/` 复制 Markdown 到新目录并补建 metadata；旧文件始终保留且不会被改写。对于已经位于新目录但缺少 JSON 的旧 Markdown，也只补 metadata，不会重新调用模型覆盖正文。

## 配置要点

- `owner_ids` 默认为空，必须显式配置授权用户。
- `generation_provider_id` 用于自动生成；留空时仅能使用触发私聊的模型。
- `persona_preset` 默认 `chihaya_anon`，可选 `factual`；角色名、用户昵称和口吻均可单独覆盖。
- 网站同步默认关闭；只有同时设置 `website_sync_enabled` 和 `website_sync_path` 才会写入目标目录，失败不会影响本地日记。
- `livingmemory_db_path` 可覆盖默认的 LivingMemory SQLite 路径。

## 当前 LivingMemory 兼容范围

离线 adapter 只读兼容含 `documents(id, text, metadata)` 的旧版 SQLite 存储，并会在缺库或字段不兼容时安全失败。实际 AstrBot/LivingMemory 运行时 API 仍需在生产环境做一次加载联调确认。

## v0.4 回顾与检索

- 周记使用 ISO 周（周一至周日），月记使用自然月；均从 daily JSON 生成，不读取原始聊天。
- 周日/月末 daily 成功落盘后会尝试生成已结束周期的总结；自动写作结束后还会补扫缺少总结的已结束周期。
- 周/月 JSON 记录 `covered_dates`、`missing_dates` 和 daily 来源。缺失日期不会阻止生成。
- 补写、重写或核心 metadata 变化不会覆盖已有总结，只会标记 `summary_stale=true`；用重写命令显式重建，并保留备份。
- `/问日记 <问题>`、`/那年今日`、`/日记项目 <名称>`、`/日记话题 <名称>` 仅限私聊。Ask Diary 先本地检索，并始终给出来源日期。
- 手动总结命令：`/补写周记 YYYY-Www`、`/重写周记 YYYY-Www`、`/补写月记 YYYY-MM`、`/重写月记 YYYY-MM`。
