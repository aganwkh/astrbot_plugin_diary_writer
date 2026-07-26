# Changelog

## 1.1.2 (unreleased)

- 强化 sparse / low_activity adaptive prompt，要求在候选存在时实际使用可追溯的近期或历史素材。
- 新增 `recent_memory_used_ids`，并把模型报告的近期 memory ID 严格限制在实际候选内。
- 分别记录 normal 与 adaptive 的真实 prompt version。
- 当模型漏报当天 structured events 时，用确定性的当天 source clusters 补齐真实 LivingMemory evidence。

## 1.1.1

- 修复 AstrBot cron 接收带时区活动时间时的 datetime 运算异常。
- 修复网站同步原子写入后索引权限变为仅 root 可读的问题。
- 修复网站索引的 Markdown 相对路径、旧版日期格式去重与排序兼容。
- 现有网站索引不可读或损坏时拒绝覆盖，避免丢失历史索引。

## 1.1.0

- 新增 daily activity tracker、`normal` / `sparse` / `low_activity` 三种 daily 模式，以及 04:10 Daily Finalization 和 v1.1 生效日后的缺口自愈。
- low_activity 保存有限私聊来源，使用可追溯的近期上下文与加权历史 memory 回忆；候选、实际使用 ID 和 30 天软冷却均持久化。
- 重写 low_activity 会复用首次来源，不会重新抽取历史记忆。
- 新状态使用原子写入，纳入 archive/restore；管理页显示 daily 模式与低活跃来源计数。

## 1.0.0

- 汇总 v0.3–v0.6 的结构化 daily、回顾与检索、年记/趋势/Plugin Page、事实纠错、归档恢复、生命周期、reflection 和完整性审计能力。
