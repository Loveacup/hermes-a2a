-- Migration 001: A2A Comment Kinds (DCI 14 类型化认知行为)
--
-- 方案 D：旁路表，与 Hermes 上游 task_comments 解耦。
-- hermes-a2a 插件拥有此表的生命周期；上游 Hermes 无需任何改动。
--
-- 关联：
--   - 上游表 task_comments.id  ← 被本表 comment_id 引用（非强制 FK，跨 schema 易脆）
--   - 路线图 §11 P0-3、DCI 论文 arXiv 2603.11781、计划 tdd-test-plan.md §3
--
-- 应用方式：
--   sqlite3 ~/.hermes/kanban.db < s6m-config/migrations/001_a2a_comment_kinds.sql
--
-- 回滚：
--   sqlite3 ~/.hermes/kanban.db < s6m-config/migrations/001_a2a_comment_kinds_rollback.sql
--
-- 幂等：本 migration 使用 IF NOT EXISTS，重复执行不抛错、不丢数据。
-- 版本：v1.0  作者：CC Agent  日期：2026-05-29

BEGIN;

-- ─────────────────────────────────────────────────────────────
-- 1. 主表：a2a_comment_kinds
-- ─────────────────────────────────────────────────────────────
-- 每行对应 task_comments 中的一条 comment，附加 DCI kind 与回复链。
-- 软外键：不在 SQLite 层做 FK（task_comments 由 Hermes 拥有，schema 可能变更），
-- 由 hermes-a2a/core/comment_kind.py 在应用层校验 comment_id 存在性。

CREATE TABLE IF NOT EXISTS a2a_comment_kinds (
    comment_id   INTEGER PRIMARY KEY,            -- 对应 task_comments.id（1:1）
    task_id      TEXT    NOT NULL,               -- 冗余存储，避开 task_comments JOIN
    kind         TEXT    NOT NULL,               -- DCI 14 枚举之一（CHECK 约束见下）
    in_reply_to  INTEGER,                        -- 同 task 下另一 comment_id；NULL = 顶层
    metadata     TEXT    NOT NULL DEFAULT '{}',  -- JSON：EVIDENCE 用 source_url、VOTE 用 weight 等
    created_at   INTEGER NOT NULL,               -- unix ts (s)，与 task_comments.created_at 对齐
    schema_ver   INTEGER NOT NULL DEFAULT 1,     -- 本 schema 版本号

    CHECK (kind IN (
        'propose',
        'ask',
        'evidence_for',
        'evidence_against',
        'challenge',
        'clarify',
        'refine',
        'concede',
        'synthesize',
        'summarize',
        'meta_directive',
        'vote_for',
        'vote_against',
        'abstain'
    )),
    CHECK (in_reply_to IS NULL OR in_reply_to != comment_id),  -- 拒绝自引用
    CHECK (json_valid(metadata))                                -- metadata 必须合法 JSON
);

-- ─────────────────────────────────────────────────────────────
-- 2. 索引：支持高频查询
-- ─────────────────────────────────────────────────────────────

-- 按 task_id 拉取整条 thread（orchestrator 路由 + dashboard 渲染）
CREATE INDEX IF NOT EXISTS idx_a2a_kinds_task_time
    ON a2a_comment_kinds(task_id, created_at);

-- 按 kind 统计（VOTE 聚合、CHALLENGE 路由到 regent）
CREATE INDEX IF NOT EXISTS idx_a2a_kinds_kind
    ON a2a_comment_kinds(kind, task_id);

-- 回复链遍历（查某条 comment 的所有直接回复）
CREATE INDEX IF NOT EXISTS idx_a2a_kinds_reply_to
    ON a2a_comment_kinds(in_reply_to)
    WHERE in_reply_to IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- 3. 视图：a2a_thread_view —— task_comments + a2a_comment_kinds 联合视图
-- ─────────────────────────────────────────────────────────────
-- 给 orchestrator / dashboard / discuss.py 用，避免每处都手写 JOIN。
-- 没有 a2a 记录的旧 comment 默认 kind='propose'（向后兼容）。

DROP VIEW IF EXISTS a2a_thread_view;
CREATE VIEW a2a_thread_view AS
SELECT
    c.id           AS comment_id,
    c.task_id      AS task_id,
    c.author       AS author,
    c.body         AS body,
    c.created_at   AS created_at,
    COALESCE(k.kind, 'propose')     AS kind,
    k.in_reply_to                   AS in_reply_to,
    COALESCE(k.metadata, '{}')      AS metadata,
    CASE WHEN k.comment_id IS NULL THEN 0 ELSE 1 END AS has_a2a_record
FROM task_comments c
LEFT JOIN a2a_comment_kinds k ON k.comment_id = c.id
ORDER BY c.task_id, c.created_at;

-- ─────────────────────────────────────────────────────────────
-- 4. 元表：a2a_schema_versions —— 记录已应用 migration
-- ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS a2a_schema_versions (
    version    INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL,
    note       TEXT
);

INSERT OR IGNORE INTO a2a_schema_versions(version, applied_at, note)
VALUES (1, strftime('%s','now'), 'DCI comment kinds bypass table');

COMMIT;

-- ─────────────────────────────────────────────────────────────
-- 验证查询（手动跑）
-- ─────────────────────────────────────────────────────────────
-- SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'a2a_%';
-- SELECT * FROM a2a_schema_versions;
-- PRAGMA index_list('a2a_comment_kinds');
-- SELECT * FROM a2a_thread_view LIMIT 5;
