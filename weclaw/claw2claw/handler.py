"""
C2C Handler — 处理其他龙虾发来的消息 (v0.8)

职责：
1. 接收远程龙虾的消息（通过 /c2c/incoming 端点）
2. 验证签名
3. 行为规则过滤（v0.8: 黑名单、信任门槛、速率限制）
4. 根据消息类型分发处理
5. Welcome Bubbles（v0.8: 首次握手自动发送问候泡泡）
6. 通知主人有龙虾来传话了
7. 记录信任事件（v0.7）

消息处理流程：
  远程龙虾 POST /c2c/incoming
    → 行为过滤（v0.8 AgentBehavior）
    → 验签（如果有 shared_secret）
    → 根据 msg_type 分发：
        handshake → 交换名片 + Welcome Bubbles，注册到 PeerRegistry，+信任事件
        message → 记录 + 通知主人 + AI 生成摘要 + 信任+2
        query → AI 生成回答 → 直接回复
        status → 更新对方状态
        ack → 标记已送达
        introduce → 处理好友引荐（v0.7）
"""

import time
from datetime import datetime
from typing import Optional, Callable
from collections import defaultdict

from loguru import logger

from weclaw.claw2claw.protocol import (
    AgentCard,
    AgentBehavior,
    C2CMessage,
    PeerInfo,
    PeerRegistry,
)


class C2CHandler:
    """
    处理收到的龙虾消息

    由 terminal.py 的 _handle_c2c_http 或 Relay 回调调用。
    """

    def __init__(
        self,
        my_card: AgentCard,
        peer_registry: PeerRegistry,
        notify_owner_fn: Optional[Callable[[str], None]] = None,
        ai_digest_fn: Optional[Callable[[str, str], str]] = None,
        state_store=None,
        behavior: Optional[AgentBehavior] = None,
    ):
        """
        Args:
            my_card: 我自己的龙虾名片
            peer_registry: 已知龙虾通讯录
            notify_owner_fn: 通知主人的回调 (message_str) -> None
            ai_digest_fn: AI 摘要/回答回调 (from_name, content) -> str
            state_store: StateStore 实例（可选，用于持久化 inbox）
            behavior: 行为规则配置（v0.8）
        """
        self.my_card = my_card
        self.peers = peer_registry
        self._notify_owner = notify_owner_fn
        self._ai_digest = ai_digest_fn
        self._store = state_store
        self._behavior = behavior or AgentBehavior()

        # 收到的消息历史（内存缓存 + 可选持久化）
        self._inbox: list[dict] = []
        if self._store:
            self._inbox = self._store.list_c2c_inbox(limit=200)

        # v0.8: 速率限制器（lobster_id → [timestamp, ...])
        self._rate_limiter: dict[str, list[float]] = defaultdict(list)

    def _check_rate_limit(self, lobster_id: str) -> bool:
        """检查消息速率（v0.8），返回 True 表示超速需拦截"""
        max_per_min = self._behavior.security.max_messages_per_minute
        if max_per_min <= 0:
            return False
        now = time.time()
        # 清理 1 分钟前的记录
        self._rate_limiter[lobster_id] = [
            t for t in self._rate_limiter[lobster_id] if now - t < 60
        ]
        if len(self._rate_limiter[lobster_id]) >= max_per_min:
            return True  # 超速
        self._rate_limiter[lobster_id].append(now)
        return False

    def _check_behavior_filter(self, incoming: C2CMessage) -> Optional[C2CMessage]:
        """
        v0.8: 行为规则过滤

        返回 None 表示通过，返回 C2CMessage 表示拦截（含拦截原因）
        """
        flt = self._behavior.filter

        # 黑名单检查
        if incoming.from_lobster_id in flt.block_list:
            logger.warning(f"🚫 黑名单拦截: {incoming.from_lobster_name}")
            return C2CMessage(
                from_lobster_id=self.my_card.lobster_id,
                from_lobster_name=self.my_card.lobster_name,
                msg_type="status",
                content="消息被拦截",
                reply_to=incoming.message_id,
            )

        # 信任门槛检查（握手消息除外）
        if flt.min_trust_to_message > 0 and incoming.msg_type != "handshake":
            peer = self.peers.get_peer(incoming.from_lobster_id)
            if peer and peer.trust_score < flt.min_trust_to_message:
                logger.warning(
                    f"🚫 信任不足: {incoming.from_lobster_name} "
                    f"(trust={peer.trust_score}, 要求>={flt.min_trust_to_message})"
                )
                return C2CMessage(
                    from_lobster_id=self.my_card.lobster_id,
                    from_lobster_name=self.my_card.lobster_name,
                    msg_type="status",
                    content="需要更高信任等级才能发送消息",
                    reply_to=incoming.message_id,
                )

        # 速率限制检查
        if self._check_rate_limit(incoming.from_lobster_id):
            logger.warning(f"🚫 速率限制: {incoming.from_lobster_name}")
            return C2CMessage(
                from_lobster_id=self.my_card.lobster_id,
                from_lobster_name=self.my_card.lobster_name,
                msg_type="status",
                content="消息太频繁，请稍后再试",
                reply_to=incoming.message_id,
            )

        # 关键词拦截
        if self._behavior.security.block_keywords:
            content_lower = incoming.content.lower()
            for kw in self._behavior.security.block_keywords:
                if kw.lower() in content_lower:
                    logger.warning(f"🚫 关键词拦截: {kw}")
                    return C2CMessage(
                        from_lobster_id=self.my_card.lobster_id,
                        from_lobster_name=self.my_card.lobster_name,
                        msg_type="status",
                        content="消息内容不符合接收规则",
                        reply_to=incoming.message_id,
                    )

        return None  # 通过

    def handle(self, incoming: C2CMessage) -> C2CMessage:
        """
        处理一条收到的 C2C 消息

        Args:
            incoming: 收到的消息

        Returns:
            回复消息（ACK / handshake 回复 / query 回复）
        """
        logger.info(
            f"🦞←🦞 收到 {incoming.msg_type} 消息 "
            f"from {incoming.from_lobster_name} (主人: {incoming.from_owner_name})"
        )

        # 0. v0.8: 行为规则过滤
        block_reply = self._check_behavior_filter(incoming)
        if block_reply:
            return block_reply

        # 1. 身份验证
        known_peer = self.peers.get_peer(incoming.from_lobster_id)

        if known_peer:
            # 已知龙虾：有密钥时必须验签
            if known_peer.shared_secret:
                if not incoming.verify(known_peer.shared_secret):
                    logger.warning(
                        f"🚫 签名验证失败: {incoming.from_lobster_name}"
                    )
                    # v0.7: 记录信任事件（验签失败 -20）
                    if self._store:
                        self._store.log_trust_event(
                            incoming.from_lobster_id, "verify_fail", -20,
                            f"签名验证失败: {incoming.msg_type}"
                        )
                    return C2CMessage(
                        from_lobster_id=self.my_card.lobster_id,
                        from_lobster_name=self.my_card.lobster_name,
                        msg_type="status",
                        content="签名验证失败，消息被拒绝",
                        reply_to=incoming.message_id,
                    )
            else:
                logger.warning(
                    f"⚠️ 已知龙虾 {incoming.from_lobster_name} 没有密钥，跳过签名验证"
                )
        else:
            # 未知龙虾：只允许 handshake 类型
            if incoming.msg_type != "handshake":
                logger.warning(
                    f"🚫 拒绝未知龙虾 {incoming.from_lobster_name} 的 {incoming.msg_type} 消息"
                )
                return C2CMessage(
                    from_lobster_id=self.my_card.lobster_id,
                    from_lobster_name=self.my_card.lobster_name,
                    msg_type="status",
                    content="请先完成握手再发送消息",
                    reply_to=incoming.message_id,
                )

        # 2. 分发处理
        if incoming.msg_type == "handshake":
            return self._handle_handshake(incoming)
        elif incoming.msg_type == "message":
            return self._handle_message(incoming)
        elif incoming.msg_type == "query":
            return self._handle_query(incoming)
        elif incoming.msg_type == "status":
            return self._handle_status(incoming)
        elif incoming.msg_type == "ack":
            return self._handle_ack(incoming)
        elif incoming.msg_type == "introduce":
            return self._handle_introduce(incoming)
        else:
            logger.warning(f"未知消息类型: {incoming.msg_type}")
            return incoming.to_ack(
                self.my_card.lobster_id,
                self.my_card.lobster_name,
            )

    # ──────────────────────────────────────────
    # 握手处理
    # ──────────────────────────────────────────

    def _handle_handshake(self, msg: C2CMessage) -> C2CMessage:
        """
        处理握手请求

        对方发来名片 → 我注册到通讯录（待确认状态） → 通知主人 → 回复我的名片 + Welcome Bubbles
        """
        peer_card_data = msg.payload.get("agent_card", {})

        # 注册/更新对方龙虾（默认 trust_score=0，需主人确认）
        peer_info = PeerInfo(
            lobster_id=peer_card_data.get("lobster_id", msg.from_lobster_id),
            lobster_name=peer_card_data.get("lobster_name", msg.from_lobster_name),
            owner_name=peer_card_data.get("owner_name", msg.from_owner_name),
            endpoint=peer_card_data.get("endpoint", msg.from_endpoint),
            capabilities=[
                c.get("name", "") for c in peer_card_data.get("capabilities", [])
            ],
            tags=peer_card_data.get("tags", []),       # v0.7
            handle=peer_card_data.get("handle", ""),   # v0.7
            description=peer_card_data.get("description", ""),  # v0.8
            trust_score=0,  # 需主人确认后提升
            last_seen=datetime.now().isoformat(),
        )

        # 如果是已知且已信任的龙虾重新握手，保持信任分
        existing = self.peers.get_peer(peer_info.lobster_id)
        is_first_contact = existing is None
        if existing and existing.trusted:
            peer_info.trust_score = existing.trust_score
            peer_info.shared_secret = existing.shared_secret

        self.peers.add_peer(peer_info)

        logger.info(
            f"🦞🤝 收到握手请求! 认识了 {peer_info.lobster_name} "
            f"(主人: {peer_info.owner_name}, trust: {peer_info.trust_score})"
        )

        # v0.7: 记录信任事件（握手成功 +10）
        if self._store:
            self._store.log_trust_event(
                peer_info.lobster_id, "handshake_ok", 10,
                f"与 {peer_info.owner_name} 的龙虾完成握手"
            )

        # v0.8: 如果对方名片中有 welcome_bubbles，显示给主人
        peer_welcome = peer_card_data.get("welcome_bubbles", [])

        # 通知主人
        if self._notify_owner:
            # 拼接对方的描述信息（v0.8）
            desc_line = f"📝 简介: {peer_info.description}\n" if peer_info.description else ""
            welcome_lines = ""
            if peer_welcome and is_first_contact:
                welcome_lines = "\n💬 对方的问候：\n" + "\n".join(
                    f"  💭 {b}" for b in peer_welcome
                ) + "\n"

            if peer_info.trusted:
                self._notify_owner(
                    f"🦞🤝 老朋友 {peer_info.lobster_name} 重新握手了\n\n"
                    f"🦞 {peer_info.lobster_name}\n"
                    f"👤 主人: {peer_info.owner_name}\n"
                    f"{desc_line}"
                    f"🎯 能力: {', '.join(peer_info.capabilities) or '未知'}\n"
                    f"🏷️ 标签: {', '.join(peer_info.tags) or '无'}\n"
                    f"💯 信任: {peer_info.trust_score}/100\n"
                    f"{welcome_lines}\n"
                    f"已更新龙虾通讯录 ✅"
                )
            else:
                self._notify_owner(
                    f"🦞🤝 新龙虾来打招呼了！\n\n"
                    f"🦞 {peer_info.lobster_name}\n"
                    f"👤 主人: {peer_info.owner_name}\n"
                    f"{desc_line}"
                    f"🔗 地址: {peer_info.endpoint}\n"
                    f"🎯 能力: {', '.join(peer_info.capabilities) or '未知'}\n"
                    f"🏷️ 标签: {', '.join(peer_info.tags) or '无'}\n"
                    f"{welcome_lines}\n"
                    f"⏳ 已添加到通讯录（信任分: {peer_info.trust_score}/100）\n"
                    f"回复 "龙虾信任 {peer_info.owner_name}" 来信任对方。"
                )

        # 回复我的名片（v0.8: payload 中包含 welcome_bubbles）
        reply_payload = {"agent_card": self.my_card.to_public()}
        reply_content = f"你好！我是 {self.my_card.owner_name} 的龙虾，很高兴认识你 🦞🤝🦞"

        # v0.8: 首次握手 → 附带 Welcome Bubbles
        if is_first_contact and self.my_card.welcome_bubbles:
            reply_content += "\n\n" + "\n".join(self.my_card.welcome_bubbles)

        return C2CMessage(
            from_lobster_id=self.my_card.lobster_id,
            from_lobster_name=self.my_card.lobster_name,
            from_owner_name=self.my_card.owner_name,
            from_endpoint=self.my_card.endpoint,
            msg_type="handshake",
            content=reply_content,
            payload=reply_payload,
            reply_to=msg.message_id,
        )

    # ──────────────────────────────────────────
    # 消息处理（代主人传话）
    # ──────────────────────────────────────────

    def _handle_message(self, msg: C2CMessage) -> C2CMessage:
        """
        处理传话消息

        对方龙虾代其主人传话 → 通知我的主人 → 回复 ACK
        """
        # 记录到收件箱
        inbox_entry = {
            "from_lobster": msg.from_lobster_name,
            "from_owner": msg.from_owner_name,
            "content": msg.content,
            "message_id": msg.message_id,
            "received_at": datetime.now().isoformat(),
            "msg_type": "message",
        }
        self._inbox.append(inbox_entry)
        if self._store:
            self._store.save_c2c_message(inbox_entry)
            # v0.7: 记录信任事件（传话成功 +2）
            self._store.log_trust_event(
                msg.from_lobster_id, "message_ok", 2,
                f"收到 {msg.from_owner_name} 的传话"
            )

        # 通知主人
        if self._notify_owner:
            self._notify_owner(
                f"🦞📨 收到来自 {msg.from_owner_name} 的龙虾传话：\n\n"
                f"「{msg.content}」\n\n"
                f"（由 {msg.from_lobster_name} 转达）"
            )

        # 回复 ACK
        return C2CMessage(
            from_lobster_id=self.my_card.lobster_id,
            from_lobster_name=self.my_card.lobster_name,
            from_owner_name=self.my_card.owner_name,
            msg_type="ack",
            content=f"已收到 {msg.from_owner_name} 的消息，我会转告我的主人 {self.my_card.owner_name}",
            reply_to=msg.message_id,
            conversation_id=msg.conversation_id,
        )

    # ──────────────────────────────────────────
    # 查询处理
    # ──────────────────────────────────────────

    def _handle_query(self, msg: C2CMessage) -> C2CMessage:
        """
        处理查询请求

        对方龙虾问了一个问题 → AI 尝试回答 → 如果不能自主回答则通知主人
        """
        # 记录到收件箱
        inbox_entry = {
            "from_lobster": msg.from_lobster_name,
            "from_owner": msg.from_owner_name,
            "content": msg.content,
            "message_id": msg.message_id,
            "received_at": datetime.now().isoformat(),
            "msg_type": "query",
        }
        self._inbox.append(inbox_entry)
        if self._store:
            self._store.save_c2c_message(inbox_entry)

        # 尝试 AI 生成回答
        answer = None
        if self._ai_digest:
            try:
                answer = self._ai_digest(msg.from_owner_name, msg.content)
            except Exception as e:
                logger.warning(f"AI 生成回答失败: {e}")

        if answer:
            # 能自主回答
            if self._notify_owner:
                self._notify_owner(
                    f"🦞❓ {msg.from_owner_name} 的龙虾问：「{msg.content}」\n"
                    f"🦞💬 我自动回复了：「{answer}」"
                )

            return C2CMessage(
                from_lobster_id=self.my_card.lobster_id,
                from_lobster_name=self.my_card.lobster_name,
                from_owner_name=self.my_card.owner_name,
                msg_type="message",
                content=answer,
                reply_to=msg.message_id,
                conversation_id=msg.conversation_id,
            )
        else:
            # 不能自主回答 → 通知主人
            if self._notify_owner:
                self._notify_owner(
                    f"🦞❓ {msg.from_owner_name} 的龙虾问了：\n\n"
                    f"「{msg.content}」\n\n"
                    f"我不确定怎么回答，需要你来决定。\n"
                    f"回复 "龙虾回 {msg.from_owner_name} <你的回答>" 来回复。"
                )

            return C2CMessage(
                from_lobster_id=self.my_card.lobster_id,
                from_lobster_name=self.my_card.lobster_name,
                from_owner_name=self.my_card.owner_name,
                msg_type="status",
                content=f"已收到你的问题，{self.my_card.owner_name} 稍后回复",
                reply_to=msg.message_id,
                conversation_id=msg.conversation_id,
            )

    # ──────────────────────────────────────────
    # 状态 & ACK
    # ──────────────────────────────────────────

    def _handle_status(self, msg: C2CMessage) -> C2CMessage:
        """处理状态更新"""
        logger.info(f"📡 状态更新 from {msg.from_lobster_name}: {msg.content}")

        # 通知主人
        if self._notify_owner:
            self._notify_owner(
                f"📡 {msg.from_owner_name} 的龙虾更新状态：{msg.content}"
            )

        return msg.to_ack(self.my_card.lobster_id, self.my_card.lobster_name)

    def _handle_ack(self, msg: C2CMessage) -> C2CMessage:
        """处理 ACK 确认"""
        logger.info(f"✅ 收到 ACK from {msg.from_lobster_name}: {msg.content}")
        # ACK 不需要再回复，返回一个简单的 ACK
        return msg.to_ack(self.my_card.lobster_id, self.my_card.lobster_name)

    # ──────────────────────────────────────────
    # v0.7: 好友引荐处理
    # ──────────────────────────────────────────

    def _handle_introduce(self, msg: C2CMessage) -> C2CMessage:
        """
        处理好友引荐（v0.7，借鉴 Tobira.ai 匹配推荐）

        payload 格式：
        {
            "introduced_peer": {
                "lobster_id": "lobster_xxx",
                "lobster_name": "...",
                "owner_name": "...",
                "tags": [...],
            },
            "reason": "引荐理由"
        }
        """
        introduced = msg.payload.get("introduced_peer", {})
        reason = msg.payload.get("reason", "")

        if not introduced.get("lobster_id"):
            return msg.to_ack(self.my_card.lobster_id, self.my_card.lobster_name)

        # 记录被引荐的龙虾到通讯录（低信任分，标记引荐来源）
        existing = self.peers.get_peer(introduced["lobster_id"])
        if not existing:
            peer_info = PeerInfo(
                lobster_id=introduced["lobster_id"],
                lobster_name=introduced.get("lobster_name", ""),
                owner_name=introduced.get("owner_name", ""),
                endpoint=introduced.get("endpoint", ""),
                tags=introduced.get("tags", []),
                trust_score=5,  # 引荐给予初始 5 分信任
                introduced_by=msg.from_lobster_id,
                last_seen=datetime.now().isoformat(),
            )
            self.peers.add_peer(peer_info)

            # v0.7: 记录信任事件
            if self._store:
                self._store.log_trust_event(
                    introduced["lobster_id"], "introduced", 5,
                    f"由 {msg.from_owner_name} 引荐"
                )

        logger.info(
            f"🦞🤝🦞 收到引荐! {msg.from_owner_name} 推荐了 "
            f"{introduced.get('owner_name', '未知')} (理由: {reason or '无'})"
        )

        # 通知主人
        if self._notify_owner:
            intro_name = introduced.get("owner_name", "未知")
            intro_tags = ", ".join(introduced.get("tags", [])) or "无"
            self._notify_owner(
                f"🦞🤝 {msg.from_owner_name} 向你推荐了一只新龙虾！\n\n"
                f"🦞 {introduced.get('lobster_name', '未命名')}\n"
                f"👤 主人: {intro_name}\n"
                f"🏷️ 标签: {intro_tags}\n"
                f"💬 推荐理由: {reason or '未说明'}\n\n"
                f"⏳ 已添加到通讯录（信任分: 5/100）\n"
                f"回复 "龙虾信任 {intro_name}" 来信任对方。"
            )

        return C2CMessage(
            from_lobster_id=self.my_card.lobster_id,
            from_lobster_name=self.my_card.lobster_name,
            from_owner_name=self.my_card.owner_name,
            msg_type="ack",
            content=f"已收到引荐，感谢 {msg.from_owner_name} 的推荐！",
            reply_to=msg.message_id,
        )

    # ──────────────────────────────────────────
    # 收件箱
    # ──────────────────────────────────────────

    def get_inbox(self, limit: int = 20) -> list[dict]:
        """获取最近收到的龙虾消息"""
        return self._inbox[-limit:]

    def inbox_count(self) -> int:
        """收件箱未处理消息数"""
        return len(self._inbox)
