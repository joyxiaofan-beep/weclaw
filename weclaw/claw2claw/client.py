"""
C2C Client — 龙虾主动联系其他龙虾

职责：
1. 向远程龙虾发送消息（HTTP POST 或 Relay 转发）
2. 执行握手流程（交换名片 + 建立信任）
3. 查询对方状态
4. 签名验证

通信方式：
- HTTP 直连模式：HTTP JSON API（需要公网 IP / ngrok）
- Relay 模式：通过 WebSocket Relay Server 转发（零配置）

当 peer.endpoint 以 "relay://" 开头时，自动走 Relay 模式。
"""

import httpx
from loguru import logger
from typing import Optional

from weclaw.claw2claw.protocol import (
    AgentCard,
    C2CMessage,
    PeerInfo,
    PeerRegistry,
)


class C2CClient:
    """
    龙虾间通信客户端

    我是龙虾A，我要联系龙虾B。
    支持两种模式：HTTP 直连 / Relay 转发。
    """

    def __init__(self, my_card: AgentCard, peer_registry: PeerRegistry, relay_client=None):
        """
        Args:
            my_card: 我自己的龙虾名片
            peer_registry: 已知龙虾通讯录
            relay_client: RelayClient 实例（可选，启用 Relay 模式）
        """
        self.my_card = my_card
        self.peers = peer_registry
        self._http = httpx.AsyncClient(timeout=15.0)
        self._relay = relay_client  # RelayClient 实例

    def set_relay_client(self, relay_client):
        """设置 Relay 客户端（延迟注入）"""
        self._relay = relay_client

    def _is_relay_peer(self, peer: PeerInfo) -> bool:
        """判断是否是 Relay 模式的 peer"""
        return peer.endpoint.startswith("relay://")

    async def close(self):
        """关闭客户端"""
        await self._http.aclose()
        if self._relay:
            await self._relay.disconnect()

    # ──────────────────────────────────────────
    # 发送消息
    # ──────────────────────────────────────────

    async def send_message(
        self,
        peer: PeerInfo,
        content: str,
        msg_type: str = "message",
        payload: dict = None,
        reply_to: str = None,
        conversation_id: str = None,
    ) -> Optional[C2CMessage]:
        """
        向远程龙虾发送一条消息

        自动选择通信模式：
        - relay:// 开头 → 通过 Relay 转发
        - http(s):// 开头 → HTTP 直连

        Args:
            peer: 目标龙虾
            content: 消息内容
            msg_type: 消息类型（message/query/status）
            payload: 额外结构化数据
            reply_to: 回复哪条消息
            conversation_id: 对话线程 ID

        Returns:
            对方返回的 C2CMessage（ACK 或实际回复），失败返回 None
        """
        msg = C2CMessage(
            from_lobster_id=self.my_card.lobster_id,
            from_lobster_name=self.my_card.lobster_name,
            from_owner_name=self.my_card.owner_name,
            from_endpoint=self.my_card.endpoint,
            msg_type=msg_type,
            content=content,
            payload=payload or {},
            reply_to=reply_to,
            conversation_id=conversation_id,
        )

        # 签名（如果有共享密钥）
        if peer.shared_secret:
            msg.sign(peer.shared_secret)

        # 根据 endpoint 选择通信模式
        if self._is_relay_peer(peer):
            return await self._send_via_relay(peer, msg)
        else:
            return await self._send_via_http(peer, msg)

    async def _send_via_relay(self, peer: PeerInfo, msg: C2CMessage) -> Optional[C2CMessage]:
        """通过 Relay 转发消息"""
        if not self._relay or not self._relay.connected:
            logger.error("🦞✖🦞 Relay 未连接，无法发送")
            return None

        logger.info(
            f"🦞→🌐→🦞 (Relay) 发送 {msg.msg_type} 到 {peer.lobster_name}"
        )

        reply = await self._relay.send_via_relay(
            peer.lobster_id,
            msg,
            wait_response=True,
            timeout=15.0,
        )

        if reply:
            logger.info(
                f"🦞←🌐←🦞 (Relay) 收到 {peer.lobster_name} 回复: "
                f"type={reply.msg_type}, content={reply.content[:50]}"
            )
            peer.last_seen = msg.timestamp

        return reply

    async def _send_via_http(self, peer: PeerInfo, msg: C2CMessage) -> Optional[C2CMessage]:
        """通过 HTTP 直连发送"""
        url = f"{peer.endpoint.rstrip('/')}/c2c/incoming"
        logger.info(
            f"🦞→🦞 发送 {msg.msg_type} 消息到 {peer.lobster_name} ({peer.endpoint})"
        )

        try:
            resp = await self._http.post(
                url,
                json=msg.model_dump(),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

            # 解析对方回复
            reply = C2CMessage(**data)
            logger.info(
                f"🦞←🦞 收到 {peer.lobster_name} 的回复: "
                f"type={reply.msg_type}, content={reply.content[:50]}"
            )

            # 更新最后通信时间
            peer.last_seen = msg.timestamp

            return reply

        except httpx.HTTPStatusError as e:
            logger.error(
                f"🦞✖🦞 发送失败 (HTTP {e.response.status_code}): "
                f"{peer.lobster_name} @ {url}"
            )
            return None
        except httpx.RequestError as e:
            logger.error(
                f"🦞✖🦞 连接失败: {peer.lobster_name} @ {url} — {type(e).__name__}: {e}"
            )
            return None
        except Exception as e:
            logger.error(f"🦞✖🦞 意外错误: {type(e).__name__}: {e}")
            return None

    # ──────────────────────────────────────────
    # 握手（建立信任）
    # ──────────────────────────────────────────

    async def handshake(
        self,
        peer_endpoint: str,
        shared_secret: str = "",
        peer_lobster_id: str = "",
    ) -> Optional[PeerInfo]:
        """
        向远程龙虾发起握手

        流程：
        1. 我发送 handshake 消息（附带我的 Agent Card）
        2. 对方回复 handshake（附带对方的 Agent Card）
        3. 双方互相注册到 PeerRegistry

        Args:
            peer_endpoint: 对方龙虾的地址（http:// 或 relay://lobster-id）
            shared_secret: 预共享密钥
            peer_lobster_id: 对方龙虾 ID（Relay 模式必须）

        Returns:
            成功则返回 PeerInfo，失败返回 None
        """
        if peer_endpoint.startswith("relay://"):
            return await self._handshake_via_relay(peer_lobster_id or peer_endpoint.replace("relay://", ""), shared_secret)
        else:
            return await self._handshake_via_http(peer_endpoint, shared_secret)

    async def _handshake_via_relay(self, peer_lobster_id: str, shared_secret: str = "") -> Optional[PeerInfo]:
        """通过 Relay 握手"""
        if not self._relay or not self._relay.connected:
            logger.error("🦞🤝❌ Relay 未连接")
            return None

        msg = C2CMessage(
            from_lobster_id=self.my_card.lobster_id,
            from_lobster_name=self.my_card.lobster_name,
            from_owner_name=self.my_card.owner_name,
            from_endpoint=f"relay://{self.my_card.lobster_id}",
            msg_type="handshake",
            content="你好，我是一只新龙虾，通过 Relay 来握手 🦞🤝🦞",
            payload={"agent_card": self.my_card.to_public()},
        )

        if shared_secret:
            msg.sign(shared_secret)

        logger.info(f"🦞🤝 (Relay) 向 {peer_lobster_id} 发起握手...")

        reply = await self._relay.send_via_relay(
            peer_lobster_id, msg, wait_response=True, timeout=15.0
        )

        if not reply or reply.msg_type != "handshake":
            logger.warning(f"握手失败: 无有效回复")
            return None

        peer_card = reply.payload.get("agent_card", {})
        if not peer_card:
            logger.warning("握手失败: 回复中没有 agent_card")
            return None

        peer_info = PeerInfo(
            lobster_id=peer_card.get("lobster_id", reply.from_lobster_id),
            lobster_name=peer_card.get("lobster_name", reply.from_lobster_name),
            owner_name=peer_card.get("owner_name", reply.from_owner_name),
            endpoint=f"relay://{peer_lobster_id}",
            shared_secret=shared_secret,
            capabilities=[c.get("name", "") for c in peer_card.get("capabilities", [])],
            trusted=True,
            last_seen=reply.timestamp,
        )

        self.peers.add_peer(peer_info)
        logger.info(f"🦞🤝✅ (Relay) 握手成功! {peer_info.lobster_name} (主人: {peer_info.owner_name})")
        return peer_info

    async def _handshake_via_http(self, peer_endpoint: str, shared_secret: str = "") -> Optional[PeerInfo]:
        """通过 HTTP 直连握手（原有逻辑）"""
        msg = C2CMessage(
            from_lobster_id=self.my_card.lobster_id,
            from_lobster_name=self.my_card.lobster_name,
            from_owner_name=self.my_card.owner_name,
            from_endpoint=self.my_card.endpoint,
            msg_type="handshake",
            content="你好，我是一只新龙虾，想跟你交换名片 🦞🤝🦞",
            payload={"agent_card": self.my_card.to_public()},
        )

        if shared_secret:
            msg.sign(shared_secret)

        url = f"{peer_endpoint.rstrip('/')}/c2c/incoming"
        logger.info(f"🦞🤝 向 {peer_endpoint} 发起握手...")

        try:
            resp = await self._http.post(
                url,
                json=msg.model_dump(),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            reply = C2CMessage(**data)

            if reply.msg_type != "handshake":
                logger.warning(f"握手失败: 对方回复类型不是 handshake，而是 {reply.msg_type}")
                return None

            # 解析对方名片
            peer_card = reply.payload.get("agent_card", {})
            if not peer_card:
                logger.warning("握手失败: 对方回复中没有 agent_card")
                return None

            peer_info = PeerInfo(
                lobster_id=peer_card.get("lobster_id", reply.from_lobster_id),
                lobster_name=peer_card.get("lobster_name", reply.from_lobster_name),
                owner_name=peer_card.get("owner_name", reply.from_owner_name),
                endpoint=peer_card.get("endpoint", peer_endpoint),
                shared_secret=shared_secret,
                capabilities=[
                    c.get("name", "") for c in peer_card.get("capabilities", [])
                ],
                trusted=True,
                last_seen=reply.timestamp,
            )

            # 注册到通讯录
            self.peers.add_peer(peer_info)

            logger.info(
                f"🦞🤝✅ 握手成功! "
                f"认识了 {peer_info.lobster_name} (主人: {peer_info.owner_name})"
            )
            return peer_info

        except httpx.HTTPStatusError as e:
            logger.error(f"🦞🤝❌ 握手 HTTP 失败 ({e.response.status_code}): {peer_endpoint}")
            return None
        except httpx.RequestError as e:
            logger.error(f"🦞🤝❌ 握手连接失败: {peer_endpoint} — {e}")
            return None
        except Exception as e:
            logger.error(f"🦞🤝❌ 握手意外错误: {type(e).__name__}: {e}")
            return None

    # ──────────────────────────────────────────
    # 便捷方法
    # ──────────────────────────────────────────

    async def relay_to_peer(
        self,
        owner_name_or_lobster_name: str,
        content: str,
    ) -> Optional[C2CMessage]:
        """
        便捷方法：找到对方龙虾并传话

        Args:
            owner_name_or_lobster_name: 对方主人名字或龙虾名字
            content: 要传达的内容

        Returns:
            对方回复的 C2CMessage，找不到人或失败返回 None
        """
        peer = self.peers.find_by_name(owner_name_or_lobster_name)
        if not peer:
            logger.warning(f"🦞 找不到龙虾: {owner_name_or_lobster_name}")
            return None

        if not peer.trusted:
            logger.warning(
                f"🦞 {peer.lobster_name} 还没有完成握手，不能直接通信"
            )
            return None

        return await self.send_message(peer, content, msg_type="message")

    async def query_peer(
        self,
        owner_name_or_lobster_name: str,
        query: str,
    ) -> Optional[C2CMessage]:
        """
        便捷方法：向对方龙虾发起查询

        例如："老王的龙虾，老王明天有没有空？"

        Args:
            owner_name_or_lobster_name: 对方主人名字或龙虾名字
            query: 查询内容
        """
        peer = self.peers.find_by_name(owner_name_or_lobster_name)
        if not peer:
            logger.warning(f"🦞 找不到龙虾: {owner_name_or_lobster_name}")
            return None

        if not peer.trusted:
            logger.warning(
                f"🦞 {peer.lobster_name} 还没有完成握手，不能查询"
            )
            return None

        return await self.send_message(peer, query, msg_type="query")
