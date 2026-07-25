# Changelog

## 1.1.0

- 新增 daily activity tracker、`normal` / `sparse` / `low_activity` 三种 daily 模式，以及 04:10 Daily Finalization 和 v1.1 生效日后的缺口自愈。
- low_activity 保存有限私聊来源，使用可追溯的近期上下文与加权历史 memory 回忆；候选、实际使用 ID 和 30 天软冷却均持久化。
- 重写 low_activity 会复用首次来源，不会重新抽取历史记忆。
- 新状态使用原子写入，纳入 archive/restore；管理页显示 daily 模式与低活跃来源计数。

## 1.0.0

- 汇总 v0.3–v0.6 的结构化 daily、回顾与检索、年记/趋势/Plugin Page、事实纠错、归档恢复、生命周期、reflection 和完整性审计能力。
