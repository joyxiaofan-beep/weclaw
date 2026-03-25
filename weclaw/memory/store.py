"""
持久化存储 — SQLite

负责：
1. 持久化 pending_messages（待确认/待处理消息）
2. 持久化 outgoing_tracker（超时追踪记录）
3. 持久化 conversation_buffer（对话上下文）

重启后自动恢复所有状态，不再"失忆"。
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from weclaw.claw2claw.crypto import local_encrypt, local_decrypt



class StateStore:
    """
    基于 SQLite 的状态持久化存储

    线程安全：每个线程使用独立连接（SQLite 不支持跨线程共享连接）。
    """

    def __init__(self, db_path: str = "data/weclaw_state.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all_connections: list[sqlite3.Connection] = []
        self._conn_lock = threading.Lock()
        self._init_db()

    @property
    def _conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            # 追踪所有线程连接，以便 close_all() 能完整清理
            with self._conn_lock:
                self._all_connections.append(self._local.conn)
        return self._local.conn

    def _init_db(self):
        """初始化数据库表"""
        conn = self._conn
        conn.executescript("""
            -- 待确认/待处理消息
            CREATE TABLE IF NOT EXISTS pending_messages (
                id TEXT PRIMARY KEY,
                data JSON NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            -- 发出消息的超时追踪
            CREATE TABLE IF NOT EXISTS outgoing_tracker (
                id TEXT PRIMARY KEY,
                target TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                topic TEXT,
                reminded INTEGER NOT NULL DEFAULT 0,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            -- 对话上下文（会话记忆）
            CREATE TABLE IF NOT EXISTS conversation_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,          -- 'owner' | 'lobster' | 'contact'
                speaker TEXT,                -- 说话人名称
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                metadata JSON               -- 额外信息（intent、target 等）
            );

            -- 🦞↔🦞 C2C 已知龙虾通讯录
            CREATE TABLE IF NOT EXISTS c2c_peers (
                lobster_id TEXT PRIMARY KEY,
                lobster_name TEXT NOT NULL DEFAULT '',
                owner_name TEXT NOT NULL DEFAULT '',
                endpoint TEXT NOT NULL,
                shared_secret TEXT NOT NULL DEFAULT '',
                capabilities JSON NOT NULL DEFAULT '[]',
                last_seen TEXT,
                trusted INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            -- 🦞↔🦞 C2C 收件箱
            CREATE TABLE IF NOT EXISTS c2c_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_lobster TEXT NOT NULL,
                from_owner TEXT NOT NULL,
                content TEXT NOT NULL,
                message_id TEXT NOT NULL,
                msg_type TEXT NOT NULL DEFAULT 'message',
                received_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            -- KV 设置表（持久龙虾号等）
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            -- 计数器
            CREATE TABLE IF NOT EXISTS counters (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            );

            -- 初始化计数器
            INSERT OR IGNORE INTO counters (name, value) VALUES ('pending', 0);
            INSERT OR IGNORE INTO counters (name, value) VALUES ('tracker', 0);

            -- 🦞 v0.7: 信任事件日志（借鉴 Tobira.ai 信誉记录）
            CREATE TABLE IF NOT EXISTS trust_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lobster_id TEXT NOT NULL,          -- 相关龙虾
                event_type TEXT NOT NULL,           -- 事件类型: handshake_ok / message_ok / verify_fail / introduced / manual_trust / manual_distrust
                delta INTEGER NOT NULL DEFAULT 0,  -- 信任分变化量（正=加分，负=扣分）
                detail TEXT,                       -- 事件描述
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_trust_events_lobster ON trust_events(lobster_id);
        """)

        # v0.7: 兼容升级旧数据库 — 为 c2c_peers 添加新列
        for col_sql in [
            "ALTER TABLE c2c_peers ADD COLUMN trust_score INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE c2c_peers ADD COLUMN tags JSON NOT NULL DEFAULT '[]'",
            "ALTER TABLE c2c_peers ADD COLUMN handle TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE c2c_peers ADD COLUMN introduced_by TEXT",
            # v0.8: 对方自我介绍
            "ALTER TABLE c2c_peers ADD COLUMN description TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                conn.execute(col_sql)
            except sqlite3.OperationalError:
                pass  # 列已存在，跳过

        # v0.8: 兼容升级旧数据库 — 为 c2c_inbox 添加线程管理列
        for col_sql in [
            "ALTER TABLE c2c_inbox ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE c2c_inbox ADD COLUMN conversation_id TEXT",
        ]:
            try:
                conn.execute(col_sql)
            except sqlite3.OperationalError:
                pass

        # v0.8: Agent Profile 持久化表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_profile (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)

        conn.commit()
        logger.info(f"📦 状态存储已初始化: {self.db_path}")

    # ──────────────────────────────────────────
    # KV Settings（持久配置，如龙虾号）
    # ──────────────────────────────────────────

    def get_setting(self, key: str) -> Optional[str]:
        """获取一个设置值"""
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str):
        """设置一个值（覆盖写入）"""
        self._conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=?, updated_at=?""",
            (key, value, datetime.now().isoformat(), value, datetime.now().isoformat()),
        )
        self._conn.commit()

    # ──────────────────────────────────────────
    # 计数器
    # ──────────────────────────────────────────

    def next_id(self, counter_name: str) -> int:
        """原子递增计数器，返回新值（线程安全）"""
        conn = self._conn
        # 使用单条 UPDATE ... RETURNING 实现原子递增+读取
        # SQLite 3.35.0+ 支持 RETURNING
        try:
            row = conn.execute(
                "UPDATE counters SET value = value + 1 WHERE name = ? RETURNING value",
                (counter_name,),
            ).fetchone()
            conn.commit()
            if row:
                return row["value"]
        except sqlite3.OperationalError:
            # SQLite 版本不支持 RETURNING，回退到序列化方式
            pass
        # 回退：用 BEGIN IMMEDIATE 确保串行化
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE counters SET value = value + 1 WHERE name = ?",
            (counter_name,),
        )
        row = conn.execute(
            "SELECT value FROM counters WHERE name = ?",
            (counter_name,),
        ).fetchone()
        conn.commit()
        return row["value"]

    # ──────────────────────────────────────────
    # Pending Messages
    # ──────────────────────────────────────────

    def save_pending(self, mid: str, data: dict):
        """保存或更新一条待处理消息"""
        conn = self._conn
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO pending_messages (id, data, created_at, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data=?, updated_at=?""",
            (mid, json.dumps(data, ensure_ascii=False), now, now,
             json.dumps(data, ensure_ascii=False), now),
        )
        conn.commit()

    def get_pending(self, mid: str) -> Optional[dict]:
        """获取一条待处理消息"""
        row = self._conn.execute(
            "SELECT data FROM pending_messages WHERE id = ?", (mid,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def delete_pending(self, mid: str):
        """删除一条待处理消息"""
        self._conn.execute("DELETE FROM pending_messages WHERE id = ?", (mid,))
        self._conn.commit()

    def list_pending(self) -> dict[str, dict]:
        """列出所有待处理消息"""
        rows = self._conn.execute("SELECT id, data FROM pending_messages").fetchall()
        return {row["id"]: json.loads(row["data"]) for row in rows}

    # ──────────────────────────────────────────
    # Outgoing Tracker
    # ──────────────────────────────────────────

    def save_tracker(self, tid: str, target: str, sent_at: str, topic: str = None):
        """保存一条追踪记录"""
        self._conn.execute(
            """INSERT INTO outgoing_tracker (id, target, sent_at, topic)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET target=?, sent_at=?, topic=?""",
            (tid, target, sent_at, topic, target, sent_at, topic),
        )
        self._conn.commit()

    def resolve_tracker(self, target_name: str) -> list[str]:
        """标记某人的所有追踪记录为已解决，返回被清除的 ID"""
        rows = self._conn.execute(
            "SELECT id FROM outgoing_tracker WHERE target = ? AND resolved = 0",
            (target_name,),
        ).fetchall()
        cleared = [row["id"] for row in rows]
        if cleared:
            self._conn.execute(
                "UPDATE outgoing_tracker SET resolved = 1 WHERE target = ? AND resolved = 0",
                (target_name,),
            )
            self._conn.commit()
        return cleared

    def mark_reminded(self, tid: str):
        """标记已提醒"""
        self._conn.execute(
            "UPDATE outgoing_tracker SET reminded = 1 WHERE id = ?", (tid,)
        )
        self._conn.commit()

    def list_active_trackers(self) -> dict[str, dict]:
        """列出所有未解决的追踪记录"""
        rows = self._conn.execute(
            "SELECT * FROM outgoing_tracker WHERE resolved = 0"
        ).fetchall()
        return {
            row["id"]: {
                "target": row["target"],
                "sent_at": row["sent_at"],
                "topic": row["topic"],
                "reminded": bool(row["reminded"]),
                "resolved": bool(row["resolved"]),
            }
            for row in rows
        }

    def list_unremarked_overdue(self, cutoff_iso: str) -> list[dict]:
        """查找超时且未提醒过的追踪记录"""
        rows = self._conn.execute(
            """SELECT id, target, sent_at, topic FROM outgoing_tracker
               WHERE resolved = 0 AND reminded = 0 AND sent_at < ?""",
            (cutoff_iso,),
        ).fetchall()
        return [
            {"id": row["id"], "target": row["target"],
             "sent_at": row["sent_at"], "topic": row["topic"]}
            for row in rows
        ]

    def count_active_trackers(self) -> int:
        """统计活跃追踪数"""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM outgoing_tracker WHERE resolved = 0"
        ).fetchone()
        return row["cnt"]

    # ──────────────────────────────────────────
    # Conversation Buffer（对话上下文）
    # ──────────────────────────────────────────

    def add_conversation(
        self,
        role: str,
        content: str,
        speaker: str = None,
        metadata: dict = None,
    ):
        """追加一条对话记录"""
        # P1-5 安全修复: content 加密后存储
        try:
            encrypted_content = local_encrypt(content)
        except Exception:
            logger.warning("⚠️ 本地加密失败，conversation content 将以明文存储")
            encrypted_content = content

        self._conn.execute(
            """INSERT INTO conversation_buffer (role, speaker, content, timestamp, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (
                role,
                speaker,
                encrypted_content,
                datetime.now().isoformat(),
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        )
        self._conn.commit()

    def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        """获取最近 N 条对话"""
        rows = self._conn.execute(
            """SELECT role, speaker, content, timestamp, metadata
               FROM conversation_buffer
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for row in reversed(rows):  # 恢复时间顺序
            entry = {
                "role": row["role"],
                "speaker": row["speaker"],
                "content": self._decrypt_content(row["content"]),
                "timestamp": row["timestamp"],
            }
            if row["metadata"]:
                entry["metadata"] = json.loads(row["metadata"])
            result.append(entry)
        return result

    def get_conversation_context_str(self, limit: int = 10) -> str:
        """
        获取格式化的对话上下文字符串（注入 AI prompt 用）

        返回类似：
        [10:30] 你: 帮我问小王数据好了没
        [10:30] 🦞: 已发送给小王
        [10:31] 小王 ←: 好了，周五给你
        """
        records = self.get_recent_conversations(limit)
        if not records:
            return "（暂无对话历史）"

        lines = []
        for r in records:
            ts = r["timestamp"][11:16]  # HH:MM
            if r["role"] == "owner":
                lines.append(f"[{ts}] 你: {r['content']}")
            elif r["role"] == "lobster":
                lines.append(f"[{ts}] 🦞: {r['content']}")
            elif r["role"] == "contact":
                name = r.get("speaker", "某人")
                lines.append(f"[{ts}] {name} →: {r['content']}")

        return "\n".join(lines)

    def trim_conversations(self, keep_recent: int = 200):
        """清理旧对话记录，只保留最近 N 条"""
        self._conn.execute(
            """DELETE FROM conversation_buffer
               WHERE id NOT IN (
                   SELECT id FROM conversation_buffer
                   ORDER BY id DESC LIMIT ?
               )""",
            (keep_recent,),
        )
        self._conn.commit()

    # ──────────────────────────────────────────
    # 🦞↔🦞 C2C Peers（已知龙虾通讯录持久化）
    # ──────────────────────────────────────────

    def save_peer(self, peer_data: dict):
        """保存或更新一只已知龙虾（v0.8: 支持 trust_score/tags/handle/description）"""
        conn = self._conn
        # v0.7: 兼容新旧格式 — trusted: bool 自动转为 trust_score
        trust_score = peer_data.get("trust_score", 0)
        if trust_score == 0 and peer_data.get("trusted"):
            trust_score = 70  # 旧格式 trusted=True → trust_score=70
        tags_json = json.dumps(peer_data.get("tags", []), ensure_ascii=False)

        # P0 安全修复: 写入前加密 shared_secret（机器绑定密钥）
        raw_secret = peer_data.get("shared_secret", "")
        encrypted_secret = local_encrypt(raw_secret) if raw_secret else ""

        conn.execute(
            """INSERT INTO c2c_peers
               (lobster_id, lobster_name, owner_name, endpoint, shared_secret,
                capabilities, last_seen, trusted, added_at,
                trust_score, tags, handle, introduced_by, description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(lobster_id) DO UPDATE SET
               lobster_name=?, owner_name=?, endpoint=?, shared_secret=?,
               capabilities=?, last_seen=?, trusted=?,
               trust_score=?, tags=?, handle=?, introduced_by=?, description=?""",
            (
                peer_data["lobster_id"], peer_data["lobster_name"], peer_data["owner_name"],
                peer_data["endpoint"], encrypted_secret,
                json.dumps(peer_data.get("capabilities", []), ensure_ascii=False),
                peer_data.get("last_seen"), 1 if trust_score >= 70 else 0,
                peer_data.get("added_at", datetime.now().isoformat()),
                trust_score, tags_json,
                peer_data.get("handle", ""), peer_data.get("introduced_by"),
                peer_data.get("description", ""),
                # UPDATE part
                peer_data["lobster_name"], peer_data["owner_name"],
                peer_data["endpoint"], encrypted_secret,
                json.dumps(peer_data.get("capabilities", []), ensure_ascii=False),
                peer_data.get("last_seen"), 1 if trust_score >= 70 else 0,
                trust_score, tags_json,
                peer_data.get("handle", ""), peer_data.get("introduced_by"),
                peer_data.get("description", ""),
            ),
        )
        conn.commit()

    def delete_peer(self, lobster_id: str) -> bool:
        """删除一只已知龙虾"""
        conn = self._conn
        cursor = conn.execute("DELETE FROM c2c_peers WHERE lobster_id = ?", (lobster_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_all_peers(self) -> list[dict]:
        """列出所有已知龙虾（v0.8: 含 trust_score/tags/handle/description）"""
        rows = self._conn.execute("SELECT * FROM c2c_peers").fetchall()
        result = []
        keys = set()
        for row in rows:
            keys = set(row.keys())
            trust_score = row["trust_score"] if "trust_score" in keys else (70 if row["trusted"] else 0)

            # P0 安全修复: 读取后解密 shared_secret（兼容旧的明文数据）
            raw_secret = row["shared_secret"]
            try:
                decrypted_secret = local_decrypt(raw_secret) if raw_secret else ""
            except (ValueError, Exception):
                # 兼容旧数据：如果解密失败，说明是升级前的明文存储
                # 直接使用原值，下次 save_peer 时会自动加密
                decrypted_secret = raw_secret
                if raw_secret:
                    logger.debug(
                        f"shared_secret 解密失败（可能是升级前的明文数据），"
                        f"将在下次保存时自动加密: {row['lobster_id']}"
                    )

            result.append({
                "lobster_id": row["lobster_id"],
                "lobster_name": row["lobster_name"],
                "owner_name": row["owner_name"],
                "endpoint": row["endpoint"],
                "shared_secret": decrypted_secret,
                "capabilities": json.loads(row["capabilities"]) if row["capabilities"] else [],
                "last_seen": row["last_seen"],
                "trusted": bool(row["trusted"]),
                "trust_score": trust_score,
                "tags": json.loads(row["tags"]) if "tags" in keys and row["tags"] else [],
                "handle": row["handle"] if "handle" in keys else "",
                "introduced_by": row["introduced_by"] if "introduced_by" in keys else None,
                "description": row["description"] if "description" in keys else "",
                "added_at": row["added_at"],
            })
        return result

    # ──────────────────────────────────────────
    # 🦞 v0.7: Trust Events（信任事件日志）
    # ──────────────────────────────────────────

    def log_trust_event(
        self,
        lobster_id: str,
        event_type: str,
        delta: int = 0,
        detail: str = "",
    ):
        """
        记录一次信任事件（v0.7，借鉴 Tobira.ai 信誉记录）

        event_type:
        - handshake_ok: 成功握手 (+10)
        - message_ok: 成功传话 (+2)
        - verify_fail: 签名验证失败 (-20)
        - introduced: 被好友引荐 (+5)
        - manual_trust: 主人手动信任 (+30)
        - manual_distrust: 主人手动取消信任 (-50)
        """
        self._conn.execute(
            """INSERT INTO trust_events (lobster_id, event_type, delta, detail)
               VALUES (?, ?, ?, ?)""",
            (lobster_id, event_type, delta, detail),
        )
        self._conn.commit()

    def get_trust_events(self, lobster_id: str, limit: int = 20) -> list[dict]:
        """获取某只龙虾的信任事件历史"""
        rows = self._conn.execute(
            """SELECT event_type, delta, detail, created_at
               FROM trust_events WHERE lobster_id = ?
               ORDER BY id DESC LIMIT ?""",
            (lobster_id, limit),
        ).fetchall()
        return [
            {
                "event_type": row["event_type"],
                "delta": row["delta"],
                "detail": row["detail"],
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def calculate_trust_score(self, lobster_id: str) -> int:
        """根据事件日志计算实际信任分（0-100 clamp）"""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(delta), 0) as total FROM trust_events WHERE lobster_id = ?",
            (lobster_id,),
        ).fetchone()
        return max(0, min(100, row["total"]))

    # ──────────────────────────────────────────
    # 🦞↔🦞 C2C Inbox（龙虾收件箱持久化）
    # ──────────────────────────────────────────

    def save_c2c_message(self, msg_data: dict):
        """保存一条收到的 C2C 消息（v0.8: 支持 is_read / conversation_id）"""
        # P1-5 安全修复: content 加密后存储
        raw_content = msg_data["content"]
        try:
            encrypted_content = local_encrypt(raw_content)
        except Exception:
            logger.warning("⚠️ 本地加密失败，content 将以明文存储")
            encrypted_content = raw_content

        self._conn.execute(
            """INSERT INTO c2c_inbox
               (from_lobster, from_owner, content, message_id, msg_type, received_at,
                is_read, conversation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_data["from_lobster"], msg_data["from_owner"],
                encrypted_content, msg_data["message_id"],
                msg_data.get("msg_type", "message"),
                msg_data.get("received_at", datetime.now().isoformat()),
                msg_data.get("is_read", 0),
                msg_data.get("conversation_id"),
            ),
        )
        self._conn.commit()

    def _decrypt_content(self, raw: str) -> str:
        """P1-5: 解密 content 字段，兼容旧明文数据"""
        if not raw:
            return raw
        try:
            return local_decrypt(raw)
        except Exception:
            return raw  # 解密失败视为旧明文数据

    def list_c2c_inbox(self, limit: int = 20) -> list[dict]:
        """获取最近收到的 C2C 消息（v0.8: 含 is_read / conversation_id）"""
        rows = self._conn.execute(
            "SELECT * FROM c2c_inbox ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        keys = set(rows[0].keys()) if rows else set()
        return [
            {
                "from_lobster": row["from_lobster"],
                "from_owner": row["from_owner"],
                "content": self._decrypt_content(row["content"]),
                "message_id": row["message_id"],
                "msg_type": row["msg_type"],
                "received_at": row["received_at"],
                "is_read": bool(row["is_read"]) if "is_read" in keys else False,
                "conversation_id": row["conversation_id"] if "conversation_id" in keys else None,
            }
            for row in reversed(rows)
        ]

    def c2c_inbox_count(self) -> int:
        """C2C 收件箱消息数"""
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM c2c_inbox").fetchone()
        return row["cnt"]

    def c2c_unread_count(self) -> int:
        """C2C 未读消息数（v0.8）"""
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM c2c_inbox WHERE is_read = 0"
        ).fetchone()
        return row["cnt"]

    # ──────────────────────────────────────────
    # 🦞 v0.8: 对话线程管理
    # ──────────────────────────────────────────

    def list_c2c_threads(self) -> list[dict]:
        """
        列出所有对话线程（按对方龙虾分组，v0.8）

        返回每个对话方的最新消息、未读数、总消息数。
        """
        rows = self._conn.execute("""
            SELECT
                from_lobster,
                from_owner,
                MAX(received_at) as last_time,
                COUNT(*) as total_count,
                SUM(CASE WHEN is_read = 0 THEN 1 ELSE 0 END) as unread_count
            FROM c2c_inbox
            GROUP BY from_lobster
            ORDER BY last_time DESC
        """).fetchall()

        threads = []
        for row in rows:
            # 获取最新一条消息内容
            latest = self._conn.execute(
                "SELECT content FROM c2c_inbox WHERE from_lobster = ? ORDER BY id DESC LIMIT 1",
                (row["from_lobster"],),
            ).fetchone()
            threads.append({
                "from_lobster": row["from_lobster"],
                "from_owner": row["from_owner"],
                "last_time": row["last_time"],
                "total_count": row["total_count"],
                "unread_count": row["unread_count"],
                "last_message": self._decrypt_content(latest["content"]) if latest else "",
            })
        return threads

    def get_thread_messages(self, lobster_id: str, limit: int = 50) -> list[dict]:
        """获取与某只龙虾的对话记录（v0.8）"""
        rows = self._conn.execute(
            "SELECT * FROM c2c_inbox WHERE from_lobster = ? ORDER BY id DESC LIMIT ?",
            (lobster_id, limit),
        ).fetchall()
        keys = set(rows[0].keys()) if rows else set()
        return [
            {
                "from_lobster": row["from_lobster"],
                "from_owner": row["from_owner"],
                "content": self._decrypt_content(row["content"]),
                "message_id": row["message_id"],
                "msg_type": row["msg_type"],
                "received_at": row["received_at"],
                "is_read": bool(row["is_read"]) if "is_read" in keys else False,
                "conversation_id": row["conversation_id"] if "conversation_id" in keys else None,
            }
            for row in reversed(rows)
        ]

    def mark_thread_read(self, lobster_id: str) -> int:
        """标记与某只龙虾的所有消息为已读（v0.8），返回标记数"""
        cursor = self._conn.execute(
            "UPDATE c2c_inbox SET is_read = 1 WHERE from_lobster = ? AND is_read = 0",
            (lobster_id,),
        )
        self._conn.commit()
        return cursor.rowcount

    def mark_message_read(self, message_id: str):
        """标记单条消息为已读（v0.8）"""
        self._conn.execute(
            "UPDATE c2c_inbox SET is_read = 1 WHERE message_id = ?",
            (message_id,),
        )
        self._conn.commit()

    # ──────────────────────────────────────────
    # 🦞 v0.8: Agent Profile 持久化（KV 存储）
    # ──────────────────────────────────────────

    def save_agent_profile(self, key: str, value: str):
        """保存一个 Agent Profile 字段（如 description, services_offered 等）"""
        self._conn.execute(
            """INSERT INTO agent_profile (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=?, updated_at=?""",
            (key, value, datetime.now().isoformat(), value, datetime.now().isoformat()),
        )
        self._conn.commit()

    def get_agent_profile(self, key: str) -> Optional[str]:
        """获取一个 Agent Profile 字段"""
        row = self._conn.execute(
            "SELECT value FROM agent_profile WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def get_all_agent_profile(self) -> dict:
        """获取所有 Agent Profile 字段"""
        rows = self._conn.execute("SELECT key, value FROM agent_profile").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def save_agent_profile_dict(self, profile: dict):
        """批量保存 Agent Profile（dict 中的值会被 json.dumps 序列化）"""
        for key, value in profile.items():
            if isinstance(value, (list, dict)):
                self.save_agent_profile(key, json.dumps(value, ensure_ascii=False))
            else:
                self.save_agent_profile(key, str(value))

    # ──────────────────────────────────────────
    # 统计
    # ──────────────────────────────────────────

    def stats(self) -> dict:
        """获取存储统计"""
        pending = self._conn.execute("SELECT COUNT(*) as cnt FROM pending_messages").fetchone()["cnt"]
        tracker = self._conn.execute("SELECT COUNT(*) as cnt FROM outgoing_tracker WHERE resolved = 0").fetchone()["cnt"]
        convos = self._conn.execute("SELECT COUNT(*) as cnt FROM conversation_buffer").fetchone()["cnt"]
        return {
            "pending_messages": pending,
            "active_trackers": tracker,
            "conversation_records": convos,
        }

    def close(self):
        """关闭所有线程的数据库连接"""
        # 关闭当前线程的连接
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
        # 关闭所有已追踪的其他线程连接
        with self._conn_lock:
            for conn in self._all_connections:
                try:
                    conn.close()
                except Exception:
                    pass  # 连接可能已被关闭
            self._all_connections.clear()
