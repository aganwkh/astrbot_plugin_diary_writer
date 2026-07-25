# 日记

基于 LivingMemory 的私密、可追溯长期 AI 日记插件。v1.1.0 以 daily JSON 与用户确认的 correction 为事实源，同时保存 Markdown 与结构化 metadata，并把日记主要事件关联到实际的 LivingMemory memory ID。

当前稳定版：[v1.1.0](https://github.com/aganwkh/astrbot_plugin_diary_writer/releases/tag/v1.1.0)

## 已验证兼容性

- AstrBot 4.26.7
- LivingMemory 2.3.6 / schema 8
- Python 3.12

升级前建议备份完整 `plugin_data`。网站同步默认关闭。

## 命令与权限

只有 `owner_ids` 中的用户可用。日记正文、测试、补写和重写只接受私聊；群聊最多允许配置中的无敏感 `/日记状态`。

| 命令 | 说明 |
| --- | --- |
| `/日记状态` | 显示非敏感运行统计 |
| `/查看日记 [YYYY-MM-DD]` | 查看私聊日记正文 |
| `/测试日记` | 只生成预览草稿，不写任何日记、状态或连续性数据 |
| `/补写日记 YYYY-MM-DD` | 为没有日记的日期生成日记 |
| `/重写日记 YYYY-MM-DD` | 重写并保留带时间戳的旧版本备份 |
| `/补写周记 YYYY-Www` / `/重写周记 YYYY-Www` | 手动生成或重写周记；重写保留旧版本备份 |
| `/补写月记 YYYY-MM` / `/重写月记 YYYY-MM` | 手动生成或重写月记；重写保留旧版本备份 |
| `/补写年记 YYYY` | 从已有 daily JSON 补生成年度回顾 |
| `/重写年记 YYYY` | 重写年度回顾并保留带时间戳的旧版本备份 |
| `/纠正日记 YYYY-MM-DD field "旧值" -> "新值"` | 对唯一命中的当前结构化事实做确定性纠正；不调用 LLM |
| `/问日记 <问题>` | 本地检索历史日记，并返回可追溯来源日期 |
| `/那年今日` | 查看往年同月同日的真实日记摘要 |
| `/日记项目 <名称>` / `/日记话题 <名称>` | 查询项目或话题的出现时间与相关事件 |

## 自动生成

- 00:00–03:59 每十分钟检查一次；默认 00:30 后才开始检查。
- 仅当授权用户已静默至少 90 分钟才生成前一天日记。
- 04:00 有独立兜底检查，默认静默阈值为 60 分钟。
- `auto_write_enabled: false` 时不注册自动任务。

可在 `_conf_schema.json` 对应的 AstrBot 插件配置中调整 `inactive_minutes`、`fallback_inactive_minutes` 和 `cron_start_delay_minutes`。

## 数据与迁移

v1.0 数据保存在 AstrBot 标准数据目录的 `plugin_data/astrbot_plugin_diary_writer/`：

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

只读 adapter 兼容含 `documents(id, text, metadata)` 的 SQLite 存储，并会在缺库或字段不兼容时安全失败。AstrBot 4.26.7 与 LivingMemory 2.3.6 / schema 8 已完成真实 Linux 生产联调；LivingMemory 数据库始终只读。

## 回顾与检索

- 周记使用 ISO 周（周一至周日），月记使用自然月；均从 daily JSON 生成，不读取原始聊天。
- 周日/月末 daily 成功落盘后会尝试生成已结束周期的总结；自动写作结束后还会补扫缺少总结的已结束周期。
- 周/月 JSON 记录 `covered_dates`、`missing_dates` 和 daily 来源。缺失日期不会阻止生成。
- 补写、重写或核心 metadata 变化不会覆盖已有总结，只会标记 `summary_stale=true`；用重写命令显式重建，并保留备份。
- `/问日记 <问题>`、`/那年今日`、`/日记项目 <名称>`、`/日记话题 <名称>` 仅限私聊。Ask Diary 先本地检索，并始终给出来源日期。
- 手动总结命令：`/补写周记 YYYY-Www`、`/重写周记 YYYY-Www`、`/补写月记 YYYY-MM`、`/重写月记 YYYY-MM`。

## 长期观察与管理页

- 年记使用自然年，保存独立 Markdown 与 JSON，并与周/月一样保留覆盖日期、缺失日期、来源、`summary_stale`、原子写入、备份和失败状态。`/补写年记 YYYY` 与 `/重写年记 YYYY` 仅限授权私聊。
- 年记的事件、项目、话题、情绪和数量统计只来自 daily JSON；monthly 仅作为高层叙事与周期变化上下文，不参与事实计数，避免重复统计。
- 趋势统计只读取 daily metadata：心情评分、常见话题/项目、活跃项目变化、每月日记/事件/未完成事项数量。它是描述性统计，不做心理诊断，也不会写回 daily 或 continuity。
- `on_this_day_reminder_enabled` 默认关闭。开启后只会在授权用户当天首次发送的普通私聊消息时提示真实的往年同日记录；命令、群聊和后续消息不会触发，监听器不阻断原消息处理。
- AstrBot 管理端提供 `diary-manager` Plugin Page，用于浏览、搜索、趋势、stale/生成状态、来源证据和手动生成。静态资源不携带日记、人物、项目、证据、凭据或配置；所有私密内容均按需通过 Dashboard 鉴权的 plugin-local API 读取。前端将 API 文本视为不可信数据，以 DOM `textContent` 渲染，不拼接到 `innerHTML`。
- 管理页的补写/重写必须显式配置 `generation_provider_id`，因为管理页请求没有私聊 UMO 可用来选择模型。

Plugin Page 已在真实 AstrBot Dashboard 验证身份中间件、bridge 加载、plugin-local API、深浅色主题和移动端布局；未认证请求不能读取私密正文。

## v1.1 每日完整性与低活跃日记

- v1.1 生效日开始，每个自然日都会在 04:10 进行 Daily Finalization：若昨天尚无正式 daily，就按当天素材尝试生成。素材不足不再是跳过理由；Provider、I/O 或 LivingMemory 读取失败仍会记录为失败并在后续流程重试。
- 插件首次启动会原子保存 v1.1 生效日期。启动和 04:10 都只补该日期至昨天的缺口，绝不自动追溯 v1.1 之前的空档；补写历史日期时只读取目标日期之前的 LivingMemory，避免时间穿越。
- daily 有三种 `entry_type`：`normal`（私聊轮数超过阈值且当天 memory 至少 3 条）、`sparse`（私聊轮数超过阈值但当天 memory 不超过 2 条）、`low_activity`（默认不超过 2 轮私聊）。三者都进入周/月/年回顾。
- `daily_activity/YYYY-MM-DD.json` 只追踪授权私聊的有效入站消息。前两轮保存用户原文与时间；第三轮起仅增加计数。AstrBot 4.26.7 没有可靠的“最终机器人回复”监听钩子，因此不会猜测或伪造 assistant 回复。生成成功后 activity 文件删除；失败时保留以便重试。
- sparse/low_activity 使用目标日前 3 天、同一私聊会话的真实 LivingMemory 作为辅助。low_activity 还会从同一私聊会话的历史 memory 中抽 1–3 条，按时间层级、importance 和 30 天软冷却加权；只有模型明确报告实际写入正文的候选 memory 才会进入冷却。
- low_activity 首次生成会把聊天来源、近期来源、随机候选及实际使用 IDs 写入 metadata。`/重写日记` 复用这些已保存来源，不重新随机。
- 管理页时间线会显示 `entry_type`；low_activity 额外显示当天轮数、聊天素材数和随机回忆来源数，不直接暴露内容。

新增配置：`low_activity_round_threshold`（默认 2）、`sparse_memory_threshold`（默认 2）、`recent_context_days`（默认 3）、`historical_memory_min_count` / `historical_memory_max_count`（默认 1/3）、`reflection_cooldown_days`（默认 30）。权重实现保持内置，避免配置膨胀。

## 事实纠错、长期归档与完整性

- `/纠正日记 YYYY-MM-DD field "旧值" -> "新值"` 只接受唯一、精确的当前字段值；不会调用 LLM，也不会顺带改写其他事实。事件事实请在 `diary-manager` 按需读取该日 metadata 后选择稳定的 `event_id` / `fact_id` 再修改。无法安全确定正文位置时，Markdown 只追加更正注记，结构化 metadata 与 correction ledger 是当前事实。
- 每次纠错或回滚都会先保留 revision。revision 记录 parent、导致它的 correction，以及 rollback 的来源和目标；correction 的 `active`、`superseded`、`rolled_back` 状态让历史链可审计。当前检索、Ask Diary、趋势与生命周期只读取已纠正的 daily metadata。
- `archive_exports/` 保存 ZIP + manifest + SHA-256 校验的手动导出；归档包含日记、reviews、revision/correction、continuity 和非敏感运行状态，不会递归打包历史 ZIP，也不会恢复设置、密码或实例配置。恢复先校验并创建 `pre_restore_snapshots/` 快照（保留最近 5 份），且在全局维护锁内进行；ZIP 路径、符号链接、文件数量、大小和压缩比均受限。
- 人物/项目生命周期、角色 reflection 与 integrity audit 都只读取 daily 当前事实。生命周期只给出“活跃 / 最近未观察到 / 未知”等观察性描述；reflection 明确标记 `subjective`，保存稳定 `source_refs`，不参与事实检索或统计；audit 的安全修复只补可确定的结构/兼容字段，绝不从 Markdown 猜测事实。
- 管理页新增纠错/修订查看与 diff/回滚、备份校验恢复、人物和项目轨迹、reflection 与完整性检查入口。所有数据按需经 AstrBot Dashboard 鉴权 API 读取，页面以 DOM `textContent` 渲染，不把日记内容或凭据放进静态文件。
