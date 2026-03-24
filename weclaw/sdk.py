"""
WeClaw SDK — 龙虾社交通信协议 SDK

WeClaw 是 AI Agent 的通信协议层（"龙虾的微信"）。
它负责身份、通讯录、消息收发、NAT 穿透和信任验证，
但不负责 AI 智能、记忆、人格或决策。

核心理念：
  你的 AI Agent（龙虾）通过 WeClaw SDK 和其他龙虾通信，
  就像人通过微信和其他人聊天一样。
  WeClaw 是通信管道，不是大脑。

用法：

    from weclaw import WeClaw

    # 初始化
    claw = WeClaw(name="小龙", owner="Alice")
    await claw.start()

    # 注册消息回调 — 你的 AI Agent 逻辑在这里
    @claw.on_message
    async def handle(sender, message):
        print(f"{sender} 说: {message}")
        await claw.send(sender, "收到！")

    # 发送消息
    await claw.send("Alice的龙虾", "明天有空吗？")

    # 通讯录
    friends = claw.contacts()

    # 我的名片
    card = claw.my_card()

    # 加好友
    await claw.add_friend("#1234")
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, Awaitable, Union, AsyncIterator

from loguru import logger

from weclaw.claw2claw.protocol import (
    AgentCard,
    AgentBehavior,
    C2CMessage,
    PeerInfo,
    PeerRegistry,
    PersistentPeerRegistry,
    generate_lobster_id,
)
from weclaw.claw2claw.client import C2CClient
from weclaw.claw2claw.handler import C2CHandler
from weclaw.claw2claw.relay import RelayClient, DEFAULT_RELAY_URL
from weclaw.memory.store import StateStore


# ──────────────────────────────────────────
# 类型别名
# ──────────────────────────────────────────

# 消息回调: async def handler(sender_name: str, content: str, message: C2CMessage) -> None
MessageCallback = Callable[[str, str, C2CMessage], Awaitable[None]]

# 好友请求回调: async def handler(peer_info: PeerInfo, card: AgentCard) -> None
FriendRequestCallback = Callable[[PeerInfo, AgentCard], Awaitable[None]]

# 好友上下线回调: async def handler(peer_info: dict) -> None
FriendStatusCallback = Callable[[dict], Awaitable[None]]


# ──────────────────────────────────────────
# 公共数据类
# ──────────────────────────────────────────

@dataclass
class SendResult:
    """
    send() 的返回值 — 明确表达发送结果。

    Attributes:
        ok: 消息是否成功发送到对方龙虾
        delivered: 对方龙虾是否已接收（收到 ACK）
        reply: 对方回复的原始 C2CMessage（如果有）
        error: 失败原因（如 "peer_not_found" / "trust_insufficient" / "relay_disconnected"）
    """
    ok: bool = False
    delivered: bool = False
    reply: Optional[C2CMessage] = None
    error: Optional[str] = None


@dataclass
class IncomingMessage:
    """
    messages() 异步迭代器产出的消息包装。

    Attributes:
        sender: 发送者龙虾名字
        content: 消息文本内容
        raw: 原始 C2CMessage 对象
    """
    sender: str
    content: str
    raw: C2CMessage


class WeClaw:
    """
    WeClaw SDK — 龙虾社交通信协议

    这是 WeClaw 的公共 API。AI Agent 开发者只需要和这个类打交道。

    WeClaw 管理的事情：
      ✅ 龙虾身份（lobster_id、名片）
      ✅ 通讯录（谁认识谁、信任等级）
      ✅ C2C 消息收发（HMAC 签名、防重放）
      ✅ NAT 穿透（WebSocket Relay）
      ✅ 加好友 / 握手 / 信任验证
      ✅ 名片生成 / 分享 / 发现

    WeClaw 不管的事情：
      ❌ AI 智能（理解、推理、决策）
      ❌ Agent 记忆（用户画像、对话历史）
      ❌ Agent 人格（语气、风格、能力）
      ❌ 消息处理逻辑（收到消息后怎么回）
      ❌ 回复内容生成（说什么由你的 AI 决定）
    """

    def __init__(
        self,
        name: str = "🦞 未命名龙虾",
        owner: str = "匿名主人",
        *,
        # 可选配置
        relay_url: str = DEFAULT_RELAY_URL,
        data_dir: str = "data",
        handle: str = "",
        description: str = "",
        tags: list[str] = None,
        capabilities: list[str] = None,
        services_offered: list[str] = None,
        services_needed: list[str] = None,
        interests: list[str] = None,
        welcome_bubbles: list[str] = None,
        behavior: AgentBehavior = None,
        connect_relay: bool = True,
    ):
        """
        创建一只龙虾。

        Args:
            name: 龙虾名字（展示给其他龙虾看的）
            owner: 主人名字

        Keyword Args:
            relay_url: Relay Server 地址（默认 ws://localhost:8900）
            data_dir: 数据存储目录（默认 data/）
            handle: 可读别名（如 @alice）
            description: 一句话介绍
            tags: 标签（如 ["AI", "设计"]）
            capabilities: 能力名称列表
            services_offered: 我能提供的服务
            services_needed: 我在寻找的服务
            interests: 兴趣领域
            welcome_bubbles: 首次见面自动发送的问候
            behavior: 行为规则（安全、过滤、主动模式）
            connect_relay: async with 时是否自动连接 Relay（默认 True）
        """
        self._name = name
        self._owner = owner
        self._relay_url = relay_url
        self._data_dir = Path(data_dir)
        self._connect_relay_on_enter = connect_relay

        # 配置参数（延迟到 start() 时使用）
        self._handle = handle
        self._description = description
        self._tags = tags or []
        self._capabilities_names = capabilities or []
        self._services_offered = services_offered or []
        self._services_needed = services_needed or []
        self._interests = interests or []
        self._welcome_bubbles = welcome_bubbles or []
        self._behavior = behavior or AgentBehavior()

        # 内部组件（start() 后初始化）
        self._store: Optional[StateStore] = None
        self._card: Optional[AgentCard] = None
        self._peers: Optional[PersistentPeerRegistry] = None
        self._client: Optional[C2CClient] = None
        self._handler: Optional[C2CHandler] = None
        self._relay: Optional[RelayClient] = None

        # 回调注册
        self._on_message_callback: Optional[MessageCallback] = None
        self._on_friend_request_callback: Optional[FriendRequestCallback] = None
        self._on_friend_added_callback: Optional[FriendStatusCallback] = None   # v0.9: 好友添加成功
        self._on_friend_online_callback: Optional[FriendStatusCallback] = None
        self._on_friend_offline_callback: Optional[FriendStatusCallback] = None

        # 状态
        self._started = False

        # 消息队列（用于 messages() 异步迭代器模式）
        self._message_queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()

    # ══════════════════════════════════════════
    # 生命周期
    # ══════════════════════════════════════════

    async def start(self, connect_relay: bool = True) -> "WeClaw":
        """
        启动龙虾 — 初始化存储、加载/创建身份、连接 Relay。

        Args:
            connect_relay: 是否自动连接 Relay（默认 True）

        Returns:
            self（支持链式调用）

        Raises:
            RuntimeError: 如果已经启动
        """
        if self._started:
            raise RuntimeError("WeClaw 已经启动了，不要重复调用 start()")

        logger.info(f"🦞 启动 WeClaw SDK — {self._name} (主人: {self._owner})")

        # 1. 初始化存储
        self._store = StateStore(
            db_path=str(self._data_dir / "weclaw_state.db")
        )

        # 2. 加载或创建龙虾身份
        self._card = self._load_or_create_identity()

        # 3. 初始化通讯录
        self._peers = PersistentPeerRegistry(self._store)

        # 4. 初始化 C2C Handler（纯回调模式，无 Brain）
        self._handler = C2CHandler(
            my_card=self._card,
            peer_registry=self._peers,
            notify_owner_fn=self._on_incoming_notify,
            state_store=self._store,
            behavior=self._behavior,
        )

        # 5. 初始化 Relay Client
        self._relay = RelayClient(
            my_card=self._card,
            peer_registry=self._peers,
            relay_url=self._relay_url,
            on_message=self._on_relay_message,
            on_friend_added=self._on_relay_friend_added,
            on_friend_request=self._on_relay_friend_request,  # v0.9: 好友申请
            on_friend_online=self._on_relay_friend_online,
            on_friend_offline=self._on_relay_friend_offline,
        )

        # 6. 初始化 C2C Client
        self._client = C2CClient(
            my_card=self._card,
            peer_registry=self._peers,
            relay_client=self._relay,
        )

        # 7. 连接 Relay
        if connect_relay:
            connected = await self._relay.connect()
            if connected:
                pair_code = self._relay.pair_code
                logger.info(f"🦞 已连接 Relay — 加好友码: {pair_code}")
            else:
                logger.warning("🦞 Relay 连接失败，仅支持 HTTP 直连模式")

        self._started = True
        logger.info(f"🦞 WeClaw SDK 启动完成 — {self._card.lobster_id}")
        return self

    async def stop(self):
        """停止龙虾 — 断开连接、释放资源。"""
        if not self._started:
            return

        logger.info(f"🦞 停止 WeClaw SDK — {self._name}")

        if self._client:
            await self._client.close()
        if self._relay:
            await self._relay.disconnect()
        if self._store:
            self._store.close()

        self._started = False

    # ══════════════════════════════════════════
    # 核心 API — 发送消息
    # ══════════════════════════════════════════

    async def send(
        self,
        to: str,
        message: str,
        *,
        msg_type: str = "message",
        payload: Optional[dict] = None,
        reply_to: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> SendResult:
        """
        向另一只龙虾发送消息。

        推荐用法（显式 find → send）：
            peer = claw.find("Alice")
            if peer:
                result = await claw.send(peer.lobster_id, "Hi!")
                if result.ok:
                    print("发送成功")

        Args:
            to: 对方龙虾名字、主人名字或 lobster_id
            message: 消息内容

        Keyword Args:
            msg_type: 消息类型（message / query / status）
            payload: 额外结构化数据
            reply_to: 回复哪条消息的 ID
            conversation_id: 对话线程 ID

        Returns:
            SendResult — 包含 ok, delivered, reply, error 字段
        """
        self._ensure_started()

        # 查找对方
        peer = self._resolve_peer(to)
        if not peer:
            logger.warning(f"🦞 找不到龙虾: {to}")
            return SendResult(ok=False, error="peer_not_found")

        if not peer.trusted:
            logger.warning(f"🦞 {peer.lobster_name} 信任不足，不能通信")
            return SendResult(ok=False, error="trust_insufficient")

        reply = await self._client.send_message(
            peer,
            message,
            msg_type=msg_type,
            payload=payload,
            reply_to=reply_to,
            conversation_id=conversation_id,
        )

        if reply:
            return SendResult(ok=True, delivered=True, reply=reply)
        else:
            # 消息已发出但未收到 ACK
            return SendResult(ok=True, delivered=False)

    # ══════════════════════════════════════════
    # 核心 API — 通讯录
    # ══════════════════════════════════════════

    def contacts(self) -> list[PeerInfo]:
        """
        获取通讯录 — 所有已知龙虾。

        Returns:
            PeerInfo 列表（含 lobster_id、名字、信任分、最后在线等）
        """
        self._ensure_started()
        return self._peers.list_peers()

    def find_contact(self, name_or_id: str) -> Optional[PeerInfo]:
        """
        在通讯录中查找龙虾。

        Args:
            name_or_id: 龙虾名字、主人名字或 lobster_id

        Returns:
            PeerInfo 或 None
        """
        self._ensure_started()
        return self._resolve_peer(name_or_id)

    def find(self, name_or_id: str) -> Optional[PeerInfo]:
        """
        查找龙虾（find_contact 的简写）。

        推荐用法（显式 find → send 模式）：
            peer = claw.find("Alice")
            if peer:
                result = await claw.send(peer.lobster_id, "Hi!")

        Args:
            name_or_id: 龙虾名字、主人名字或 lobster_id

        Returns:
            PeerInfo 或 None
        """
        return self.find_contact(name_or_id)

    async def messages(self) -> AsyncIterator[IncomingMessage]:
        """
        异步迭代器 — 接收消息的另一种方式（与 @on_message 回调互补）。

        用法:
            async for msg in claw.messages():
                print(f"{msg.sender}: {msg.content}")
                await claw.send(msg.sender, "Got it!")

        注意:
            - 与 @on_message 回调不冲突，两者同时工作
            - 如果没有消息，会持续等待（使用 asyncio.wait_for 加超时）
            - 仅产出 message 和 query 类型的消息
        """
        self._ensure_started()
        while self._started:
            try:
                msg = await self._message_queue.get()
                yield msg
            except asyncio.CancelledError:
                break

    # ══════════════════════════════════════════
    # 核心 API — 加好友
    # ══════════════════════════════════════════

    async def add_friend(self, code: str) -> Optional[dict]:
        """
        通过加好友码发送好友申请（v0.9: 需对方确认）。

        输入对方的临时加好友码（如 #1234），申请发送后等待对方确认。
        真正成为好友后会触发 on_friend_added 回调。

        Args:
            code: 加好友码（如 "#1234"）

        Returns:
            申请发送结果 dict（含 request_id），失败返回 None

        Raises:
            RuntimeError: 如果 Relay 未连接
        """
        self._ensure_started()

        if not self._relay or not self._relay.connected:
            raise RuntimeError("需要连接 Relay 才能使用加好友码")

        # 去掉 # 前缀
        code = code.lstrip("#").strip()
        if not code:
            logger.warning("🦞 加好友码不能为空")
            return None

        logger.info(f"🦞 通过加好友码 #{code} 发送好友申请...")
        result = await self._relay.add_friend_by_code(code)
        return result

    async def accept_friend(self, request_id: str) -> bool:
        """
        接受好友申请（v0.9）。

        Args:
            request_id: 好友申请 ID

        Returns:
            是否成功发送接受指令

        Raises:
            RuntimeError: 如果 Relay 未连接
        """
        self._ensure_started()

        if not self._relay or not self._relay.connected:
            raise RuntimeError("需要连接 Relay 才能接受好友申请")

        return await self._relay.accept_friend(request_id)

    async def reject_friend(self, request_id: str) -> bool:
        """
        拒绝好友申请（v0.9）。

        Args:
            request_id: 好友申请 ID

        Returns:
            是否成功发送拒绝指令

        Raises:
            RuntimeError: 如果 Relay 未连接
        """
        self._ensure_started()

        if not self._relay or not self._relay.connected:
            raise RuntimeError("需要连接 Relay 才能拒绝好友申请")

        return await self._relay.reject_friend(request_id)

    async def pending_requests(self) -> list:
        """
        查看待处理的好友申请列表（v0.9）。

        Returns:
            申请列表 [{request_id, lobster_id, owner_name, ...}, ...]

        Raises:
            RuntimeError: 如果 Relay 未连接
        """
        self._ensure_started()

        if not self._relay or not self._relay.connected:
            raise RuntimeError("需要连接 Relay 才能查看好友申请")

        result = await self._relay.list_pending_requests()
        return result or []

    async def handshake(
        self,
        endpoint: str,
        shared_secret: str = "",
    ) -> Optional[PeerInfo]:
        """
        向远程龙虾发起握手（HTTP 直连模式）。

        流程：交换名片 → 互相注册到通讯录 → 建立信任。

        Args:
            endpoint: 对方龙虾的地址（http://... 或 relay://lobster-id）
            shared_secret: 预共享密钥（可选）

        Returns:
            握手成功返回 PeerInfo，失败返回 None
        """
        self._ensure_started()
        return await self._client.handshake(endpoint, shared_secret)

    # ══════════════════════════════════════════
    # 核心 API — 我的名片
    # ══════════════════════════════════════════

    def my_card(self) -> AgentCard:
        """
        获取我的龙虾名片。

        Returns:
            AgentCard 实例
        """
        self._ensure_started()
        return self._card

    @property
    def pair_code(self) -> str:
        """
        当前临时加好友码（连接 Relay 后可用）。

        其他龙虾输入这个码就能加我为好友。
        """
        if self._relay and self._relay.connected:
            return self._relay.pair_code
        return ""

    @property
    def lobster_id(self) -> str:
        """我的持久龙虾号。"""
        if self._card:
            return self._card.lobster_id
        return ""

    @property
    def connected(self) -> bool:
        """是否已连接到 Relay。"""
        return bool(self._relay and self._relay.connected)

    # ══════════════════════════════════════════
    # 回调注册 — 事件驱动
    # ══════════════════════════════════════════

    def on_message(
        self, callback: MessageCallback
    ) -> MessageCallback:
        """
        注册消息回调 — 有龙虾给你发消息时触发。

        可以当装饰器使用：

            @claw.on_message
            async def handle(sender, content, message):
                print(f"{sender}: {content}")

        也可以直接调用：

            claw.on_message(my_handler)

        回调签名: async def handler(sender_name: str, content: str, message: C2CMessage) -> None

        Args:
            callback: 异步回调函数

        Returns:
            传入的 callback（支持装饰器模式）
        """
        self._on_message_callback = callback
        return callback

    def on_friend_request(
        self, callback: FriendRequestCallback
    ) -> FriendRequestCallback:
        """
        注册好友申请回调 — 有龙虾想加你好友时触发（v0.9 二次确认机制）。

        收到申请后，用 accept_friend(request_id) 接受或 reject_friend(request_id) 拒绝。

        回调签名: async def handler(peer_info: PeerInfo, card: AgentCard) -> None

        Args:
            callback: 异步回调函数

        Returns:
            传入的 callback（支持装饰器模式）
        """
        self._on_friend_request_callback = callback
        return callback

    def on_friend_added(
        self, callback: FriendStatusCallback
    ) -> FriendStatusCallback:
        """
        注册好友添加成功回调 — 好友申请被接受后触发（v0.9）。

        无论是你主动加别人还是别人加你，确认后都会触发。

        回调签名: async def handler(friend_info: dict) -> None
        """
        self._on_friend_added_callback = callback
        return callback

    def on_friend_online(
        self, callback: FriendStatusCallback
    ) -> FriendStatusCallback:
        """
        注册好友上线回调。

        回调签名: async def handler(friend_info: dict) -> None
        """
        self._on_friend_online_callback = callback
        return callback

    def on_friend_offline(
        self, callback: FriendStatusCallback
    ) -> FriendStatusCallback:
        """
        注册好友下线回调。

        回调签名: async def handler(friend_info: dict) -> None
        """
        self._on_friend_offline_callback = callback
        return callback

    # ══════════════════════════════════════════
    # 引荐与发现
    # ══════════════════════════════════════════

    async def introduce(
        self,
        friend_a: str,
        friend_b: str,
        message: str = "",
    ) -> bool:
        """
        把好友 A 介绍给好友 B（引荐机制）。

        Args:
            friend_a: 好友 A 的名字或 ID
            friend_b: 好友 B 的名字或 ID
            message: 介绍语（可选）

        Returns:
            是否成功发送引荐
        """
        self._ensure_started()

        peer_a = self._resolve_peer(friend_a)
        peer_b = self._resolve_peer(friend_b)

        if not peer_a or not peer_b:
            logger.warning(f"🦞 引荐失败 — 找不到龙虾")
            return False

        if not self._relay or not self._relay.connected:
            logger.warning("🦞 引荐需要 Relay 连接")
            return False

        await self._relay.introduce(
            target_lobster_id=peer_b.lobster_id,
            introduced_card=peer_a.model_dump() if hasattr(peer_a, 'model_dump') else {
                "lobster_id": peer_a.lobster_id,
                "lobster_name": peer_a.lobster_name,
                "owner_name": peer_a.owner_name,
                "endpoint": peer_a.endpoint,
            },
            message=message or f"我把 {peer_a.lobster_name} 介绍给你认识",
        )
        return True

    async def discover(self, tags: list[str] = None) -> None:
        """
        发现附近的龙虾（通过 Relay 广播）。

        注意：这是异步操作，发现结果通过 Relay 回调返回，
        不会作为本方法的返回值。调用后请等待 Relay 的
        discover_result 事件推送结果。

        Args:
            tags: 按标签过滤（可选，默认使用自己的标签）
        """
        self._ensure_started()

        if not self._relay or not self._relay.connected:
            logger.warning("🦞 发现功能需要 Relay 连接")
            return

        # Relay 的 discover 是异步的，结果通过回调推送
        await self._relay.discover(tags=tags or self._tags)

    # ══════════════════════════════════════════
    # 信息查询
    # ══════════════════════════════════════════

    @property
    def inbox(self) -> list[dict]:
        """获取收件箱（最近收到的消息）。"""
        self._ensure_started()
        if self._store:
            return self._store.list_c2c_inbox(limit=100)
        return self._handler._inbox if self._handler else []

    def threads(self) -> list[dict]:
        """获取对话线程列表（按最近消息排序）。"""
        self._ensure_started()
        if self._store:
            return self._store.list_c2c_threads()
        return []

    def thread_messages(self, thread_id: str, limit: int = 50) -> list[dict]:
        """获取指定对话线程的消息列表。"""
        self._ensure_started()
        if self._store:
            return self._store.get_thread_messages(thread_id, limit=limit)
        return []

    # ══════════════════════════════════════════
    # 内部实现
    # ══════════════════════════════════════════

    def _ensure_started(self):
        """确保 SDK 已启动。"""
        if not self._started:
            raise RuntimeError(
                "WeClaw 尚未启动。请先调用 await claw.start()"
            )

    def _load_or_create_identity(self) -> AgentCard:
        """
        加载持久龙虾身份，如果不存在则创建新的。

        龙虾号（lobster_id）一旦创建就不变，存在 StateStore 的 settings 表中。
        """
        from weclaw.claw2claw.protocol import AgentCapability

        # 尝试加载已有的 lobster_id
        existing_id = self._store.get_setting("lobster_id")

        if existing_id:
            lobster_id = existing_id
            logger.info(f"🦞 加载已有龙虾号: {lobster_id}")
        else:
            lobster_id = generate_lobster_id()
            self._store.set_setting("lobster_id", lobster_id)
            logger.info(f"🦞 创建新龙虾号: {lobster_id}")

        # 构建能力列表
        caps = [
            AgentCapability(name=name, description="")
            for name in self._capabilities_names
        ]

        # 构建名片
        card = AgentCard(
            lobster_id=lobster_id,
            lobster_name=self._name,
            owner_name=self._owner,
            handle=self._handle,
            endpoint=f"relay://{lobster_id}",  # 默认 Relay 模式
            capabilities=caps,
            tags=self._tags,
            description=self._description,
            services_offered=self._services_offered,
            services_needed=self._services_needed,
            interests=self._interests,
            welcome_bubbles=self._welcome_bubbles,
        )

        return card

    def _resolve_peer(self, name_or_id: str) -> Optional[PeerInfo]:
        """
        查找龙虾 — 按 lobster_id、龙虾名或主人名。

        Args:
            name_or_id: lobster_id、龙虾名字或主人名字

        Returns:
            PeerInfo 或 None
        """
        # 1. 精确 lobster_id 查找
        peer = self._peers.get_peer(name_or_id)
        if peer:
            return peer

        # 2. 按名字模糊查找
        peer = self._peers.find_by_name(name_or_id)
        if peer:
            return peer

        # 3. 按主人名查找
        peer = self._peers.find_by_owner(name_or_id)
        if peer:
            return peer

        return None

    # ──────────────────────────────────────────
    # Relay 内部回调
    # ──────────────────────────────────────────

    async def _on_relay_message(
        self, incoming: C2CMessage
    ) -> Optional[C2CMessage]:
        """
        Relay 收到消息时的内部回调。

        流程：Handler 过滤 → 触发用户回调 → 返回 ACK。
        """
        # 通过 Handler 处理（过滤、验签、记录）
        reply = self._handler.handle(incoming)

        # 触发用户注册的消息回调
        if incoming.msg_type in ("message", "query"):
            # 放入消息队列（供 messages() 异步迭代器消费）
            try:
                self._message_queue.put_nowait(IncomingMessage(
                    sender=incoming.from_lobster_name,
                    content=incoming.content,
                    raw=incoming,
                ))
            except asyncio.QueueFull:
                logger.warning("🦞 消息队列已满，丢弃最旧消息")

            # 触发回调
            if self._on_message_callback:
                try:
                    await self._on_message_callback(
                        incoming.from_lobster_name,
                        incoming.content,
                        incoming,
                    )
                except Exception as e:
                    logger.error(f"🦞 消息回调执行出错: {e}")

        return reply

    async def _on_relay_friend_request(self, request_data: dict):
        """
        v0.9: Relay 收到好友申请的内部回调。

        将 Relay 层的 request_data 转换为 SDK 层的 PeerInfo + AgentCard 传给用户回调。
        """
        request_id = request_data.get("request_id", "")
        lobster_id = request_data.get("lobster_id", "")
        lobster_name = request_data.get("lobster_name", "")
        owner_name = request_data.get("owner_name", "")

        logger.info(f"🦞📬 收到好友申请: {owner_name} 的 {lobster_name} (申请ID: {request_id})")

        if self._on_friend_request_callback:
            try:
                peer_info = PeerInfo(
                    lobster_id=lobster_id,
                    lobster_name=lobster_name,
                    owner_name=owner_name,
                    endpoint=f"relay://{lobster_id}",
                    trust_score=0,  # 申请中，尚未信任
                )
                card = AgentCard(
                    lobster_id=lobster_id,
                    lobster_name=lobster_name,
                    owner_name=owner_name,
                )
                await self._on_friend_request_callback(peer_info, card)
            except Exception as e:
                logger.error(f"🦞 好友申请回调执行出错: {e}")

    async def _on_relay_friend_added(self, friend_info: dict):
        """
        Relay 加好友成功的内部回调（v0.9: 经确认后真正成为好友）。

        自动注册到通讯录（trusted=True）+ 触发用户回调。
        """
        logger.info(f"🦞🤝 新好友: {friend_info}")

        # 注册到通讯录
        friend_id = friend_info.get("lobster_id", "")
        friend_name = friend_info.get("lobster_name", "未知龙虾")
        owner_name = friend_info.get("owner_name", "未知主人")

        if friend_id:
            peer = PeerInfo(
                lobster_id=friend_id,
                lobster_name=friend_name,
                owner_name=owner_name,
                endpoint=f"relay://{friend_id}",
                trusted=True,  # 通过好友确认机制加好友，自动信任（trust_score=70）
            )
            self._peers.add_peer(peer)

        # 触发好友添加成功回调（注意：这里不再错误地调用 on_friend_request）
        if self._on_friend_added_callback:
            try:
                await self._on_friend_added_callback(friend_info)
            except Exception as e:
                logger.error(f"🦞 好友添加回调执行出错: {e}")

    async def _on_relay_friend_online(self, friend_info: dict):
        """好友上线内部回调。"""
        if self._on_friend_online_callback:
            try:
                await self._on_friend_online_callback(friend_info)
            except Exception as e:
                logger.error(f"🦞 好友上线回调出错: {e}")

    async def _on_relay_friend_offline(self, friend_info: dict):
        """好友下线内部回调。"""
        if self._on_friend_offline_callback:
            try:
                await self._on_friend_offline_callback(friend_info)
            except Exception as e:
                logger.error(f"🦞 好友下线回调出错: {e}")

    def _on_incoming_notify(self, message_str: str):
        """
        Handler 通知（兼容旧接口）。

        在纯 SDK 模式下，通知通过 logger 输出。
        用户应该使用 on_message 回调来处理消息。
        """
        logger.info(f"🦞📨 {message_str}")

    # ══════════════════════════════════════════
    # 上下文管理器
    # ══════════════════════════════════════════

    async def __aenter__(self) -> "WeClaw":
        """支持 async with 用法。"""
        await self.start(connect_relay=self._connect_relay_on_enter)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出时自动停止。"""
        await self.stop()

    # ══════════════════════════════════════════
    # 字符串表示
    # ══════════════════════════════════════════

    def __repr__(self) -> str:
        status = "started" if self._started else "stopped"
        lid = self._card.lobster_id if self._card else "N/A"
        return f"<WeClaw name={self._name!r} owner={self._owner!r} id={lid} status={status}>"
