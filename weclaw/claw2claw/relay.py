"""
C2C Relay Client v2 — 通过 Relay Server 实现龙虾互联

无需公网 IP、无需 ngrok、无需任何端口映射。
龙虾主动 WebSocket 连出去连接 Relay，Relay 负责转发。

v2 核心变化（对齐 Relay Server v2 协议）：
- 持久龙虾号（lobster_id）— 客户端生成并本地保存，重启不变
- 临时加好友码（#XXXX）— 仅首次加好友用，加完即弃
- 好友关系由客户端维护 — 注册时上报好友列表，Relay 用于路由鉴权
- 好友上/下线通知 — friend_online / friend_offline
- 用 friend_added 替代旧的 paired 事件

核心流程：
1. 启动时自动连接 Relay Server (WebSocket)
2. 注册（上报龙虾号 + 好友列表）→ 获得临时加好友码 (#XXXX)
3. 对方输入加好友码 → 双方互加好友
4. 后续直接按龙虾号路由转发消息（无需再配对）
5. 断线自动重连

用法（对终端用户透明）：
    龙虾启动 → 自动连 relay → 打印加好友码
    龙虾加好友 #1234 → 一次性加好友
    龙虾传话 Alice xxx → 按龙虾号通过 relay 转发
"""

import asyncio
import json
import time
from typing import Optional, Callable, Awaitable

from loguru import logger

from weclaw.claw2claw.protocol import (
    AgentCard,
    C2CMessage,
    PeerInfo,
    PeerRegistry,
)


# 默认公共 Relay 地址
DEFAULT_RELAY_URL = "ws://localhost:8900"


class RelayClient:
    """
    龙虾端 Relay 客户端 v2

    通过 WebSocket 连接 Relay Server，实现：
    - 注册（上报好友列表）& 获取临时加好友码
    - 通过加好友码添加新好友（一次性）
    - 好友间消息收发（经 Relay 按龙虾号路由转发）
    - 好友上/下线通知
    """

    def __init__(
        self,
        my_card: AgentCard,
        peer_registry: PeerRegistry,
        relay_url: str = DEFAULT_RELAY_URL,
        on_message: Optional[Callable[[C2CMessage], Awaitable[Optional[C2CMessage]]]] = None,
        on_friend_added: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_friend_request: Optional[Callable[[dict], Awaitable[None]]] = None,   # v0.9: 好友申请
        on_friend_online: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_friend_offline: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_discover_result: Optional[Callable[[dict], Awaitable[None]]] = None,   # v0.7
        on_introduction: Optional[Callable[[dict], Awaitable[None]]] = None,       # v0.7
    ):
        """
        Args:
            my_card: 我的龙虾名片
            peer_registry: 通讯录（持久化好友关系）
            relay_url: Relay Server 的 WebSocket 地址
            on_message: 收到消息的回调 (C2CMessage) -> Optional[C2CMessage 回复]
            on_friend_added: 加好友成功的回调 (friend_info_dict) -> None
            on_friend_request: v0.9 收到好友申请的回调 (request_data) -> None
            on_friend_online: 好友上线的回调 (friend_info_dict) -> None
            on_friend_offline: 好友下线的回调 (friend_info_dict) -> None
            on_discover_result: v0.7 发现结果回调 (discover_data) -> None
            on_introduction: v0.7 好友引荐回调 (introduction_data) -> None
        """
        self.my_card = my_card
        self.peers = peer_registry
        self.relay_url = relay_url

        self._on_message = on_message
        self._on_friend_added = on_friend_added
        self._on_friend_request = on_friend_request     # v0.9: 好友申请回调
        self._on_friend_online = on_friend_online
        self._on_friend_offline = on_friend_offline
        self._on_discover_result = on_discover_result   # v0.7
        self._on_introduction = on_introduction         # v0.7

        self._ws = None
        self._connected = False
        self._pair_code: str = ""     # 临时加好友码 (#XXXX)
        self._running = False

        # 消息等待队列（用于同步请求-响应模式）
        self._pending_responses: dict[str, asyncio.Future] = {}

        # v0.9: 本地缓存的待处理好友申请 (request_id -> request_data)
        self._pending_friend_requests: dict[str, dict] = {}

        # 重连配置
        self._reconnect_delay = 2  # 初始重连延迟（秒）
        self._max_reconnect_delay = 60
        self._heartbeat_task = None
        self._listener_task = None

    @property
    def pair_code(self) -> str:
        """当前临时加好友码"""
        return self._pair_code

    # 兼容旧代码
    @property
    def invite_code(self) -> str:
        """兼容旧属性名 → pair_code"""
        return self._pair_code

    @property
    def connected(self) -> bool:
        """是否已连接到 Relay"""
        return self._connected

    async def connect(self) -> bool:
        """
        连接到 Relay Server 并注册

        Returns:
            是否成功连接并注册
        """
        try:
            import websockets
        except ImportError:
            logger.error("需要安装 websockets: pip install websockets")
            return False

        self._running = True
        delay = self._reconnect_delay

        while self._running:
            try:
                logger.info(f"🌐 连接 Relay: {self.relay_url}")
                self._ws = await websockets.connect(
                    self.relay_url,
                    ping_interval=30,
                    ping_timeout=60,
                    close_timeout=5,
                )
                self._connected = True
                delay = self._reconnect_delay  # 重置重连延迟

                # 注册
                registered = await self._register()
                if not registered:
                    logger.error("注册失败")
                    await self._ws.close()
                    self._connected = False
                    return False

                # 启动消息监听和心跳
                self._listener_task = asyncio.create_task(self._listen())
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                return True

            except Exception as e:
                self._connected = False
                logger.warning(f"🌐 Relay 连接失败: {e}")
                if not self._running:
                    return False
                logger.info(f"   {delay}秒后重试...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._max_reconnect_delay)

        return False

    async def disconnect(self):
        """断开 Relay 连接"""
        self._running = False
        self._connected = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._listener_task:
            self._listener_task.cancel()

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # 清理等待中的 Future
        for fut in self._pending_responses.values():
            if not fut.done():
                fut.cancel()
        self._pending_responses.clear()

    async def _register(self) -> bool:
        """
        向 Relay 注册（v2 协议）

        v2 变化：
        - 发送 friends 列表（从本地通讯录读取已有好友的 lobster_id）
        - 返回 pair_code（临时加好友码）替代旧的 invite_code
        - 返回 online_friends（当前在线的好友列表）
        """
        # 从本地通讯录提取好友 ID 列表
        friends_ids = [p.lobster_id for p in self.peers.list_peers() if p.trusted]

        msg = {
            "type": "register",
            "data": {
                "lobster_id": self.my_card.lobster_id,
                "lobster_name": self.my_card.lobster_name,
                "owner_name": self.my_card.owner_name,
                "friends": friends_ids,
                "tags": self.my_card.tags,       # v0.7: 技能标签（用于发现）
                "handle": self.my_card.handle,   # v0.7: 可读别名
                # v0.8: enriched profile for discovery
                "description": self.my_card.description,
                "services_offered": self.my_card.services_offered,
                "interests": self.my_card.interests,
                "industries": self.my_card.industries,
                "location_area": self.my_card.location_area,
            }
        }

        await self._send(msg)

        # 等待注册回复
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            reply = json.loads(raw)
            if reply.get("type") == "registered":
                self._pair_code = reply["data"].get("pair_code", "")
                online_friends = reply["data"].get("online_friends", [])
                logger.info(
                    f"✅ Relay 注册成功! 加好友码: {self._pair_code} "
                    f"在线好友: {len(online_friends)}"
                )
                # 通知在线好友
                for f in online_friends:
                    if self._on_friend_online:
                        await self._on_friend_online(f)
                return True
            else:
                logger.error(f"注册失败: {reply}")
                return False
        except asyncio.TimeoutError:
            logger.error("注册超时")
            return False

    async def add_friend_by_code(self, pair_code: str) -> Optional[dict]:
        """
        通过临时加好友码发送好友申请（v0.9 二次确认机制）

        流程: 输入配对码 → 服务器发申请给对方 → 等对方确认/拒绝
        返回: {request_id, lobster_id, owner_name, message} 或 None（失败）

        注意: 返回成功仅表示"申请已发送"，不表示已成为好友。
              真正成为好友会通过 on_friend_added 回调通知。

        Args:
            pair_code: 对方的加好友码 (#XXXX)

        Returns:
            申请发送结果 dict，失败返回 None
        """
        if not self._connected:
            logger.error("未连接到 Relay")
            return None

        # 创建一个 Future 等待申请发送结果
        pair_future = asyncio.get_running_loop().create_future()
        self._pending_responses["_pair_result"] = pair_future

        # 规范化加好友码
        code = pair_code.strip()
        if not code.startswith("#"):
            code = "#" + code

        msg = {
            "type": "pair",
            "data": {"pair_code": code}
        }
        await self._send(msg)

        try:
            result = await asyncio.wait_for(pair_future, timeout=15)
            return result
        except asyncio.TimeoutError:
            logger.error("发送好友申请超时")
            return None
        finally:
            self._pending_responses.pop("_pair_result", None)

    async def accept_friend(self, request_id: str) -> bool:
        """
        接受好友申请（v0.9）

        Args:
            request_id: 好友申请 ID

        Returns:
            是否成功发送接受指令
        """
        if not self._connected:
            logger.error("未连接到 Relay")
            return False

        msg = {
            "type": "friend_accept",
            "data": {"request_id": request_id}
        }
        await self._send(msg)

        # 从本地缓存删除
        self._pending_friend_requests.pop(request_id, None)
        return True

    async def reject_friend(self, request_id: str) -> bool:
        """
        拒绝好友申请（v0.9）

        Args:
            request_id: 好友申请 ID

        Returns:
            是否成功发送拒绝指令
        """
        if not self._connected:
            logger.error("未连接到 Relay")
            return False

        msg = {
            "type": "friend_reject",
            "data": {"request_id": request_id}
        }
        await self._send(msg)

        # 从本地缓存删除
        self._pending_friend_requests.pop(request_id, None)
        return True

    async def list_pending_requests(self) -> Optional[list]:
        """
        查询待处理的好友申请列表（v0.9）

        Returns:
            申请列表 [{request_id, lobster_id, owner_name, ...}, ...] 或 None
        """
        if not self._connected:
            logger.error("未连接到 Relay")
            return None

        # 创建 Future 等待结果
        fut = asyncio.get_running_loop().create_future()
        self._pending_responses["_pending_requests_result"] = fut

        await self._send({"type": "pending_requests", "data": {}})

        try:
            result = await asyncio.wait_for(fut, timeout=10)
            return result
        except asyncio.TimeoutError:
            logger.error("查询待处理申请超时")
            return None
        finally:
            self._pending_responses.pop("_pending_requests_result", None)

    async def add_friend_by_id(self, lobster_id: str) -> Optional[dict]:
        """
        通过龙虾号发送好友申请（v0.9 二次确认机制）

        流程: 输入目标龙虾号 → 服务器查找目标 → 发申请给对方 → 等对方确认/拒绝
        返回: {request_id, lobster_id, owner_name, message} 或 None（失败）

        注意: 返回成功仅表示"申请已发送"，不表示已成为好友。
              真正成为好友会通过 on_friend_added 回调通知。

        Args:
            lobster_id: 目标龙虾号（如 claw_alice）

        Returns:
            申请发送结果 dict，失败返回 None
        """
        if not self._connected:
            logger.error("未连接到 Relay")
            return None

        # 创建一个 Future 等待申请发送结果
        pair_future = asyncio.get_running_loop().create_future()
        self._pending_responses["_pair_result"] = pair_future

        msg = {
            "type": "pair_by_id",
            "data": {"lobster_id": lobster_id.strip()}
        }
        await self._send(msg)

        try:
            result = await asyncio.wait_for(pair_future, timeout=15)
            return result
        except asyncio.TimeoutError:
            logger.error("发送好友申请超时")
            return None
        finally:
            self._pending_responses.pop("_pair_result", None)

    # 兼容旧方法名
    async def pair_with_code(self, code: str) -> Optional[dict]:
        """兼容旧方法名 → add_friend_by_code"""
        return await self.add_friend_by_code(code)

    async def send_via_relay(
        self,
        peer_lobster_id: str,
        c2c_message: C2CMessage,
        wait_response: bool = True,
        timeout: float = 15.0,
    ) -> Optional[C2CMessage]:
        """
        通过 Relay 发送 C2C 消息（按龙虾号路由）

        Args:
            peer_lobster_id: 目标龙虾号
            c2c_message: C2C 消息
            wait_response: 是否等待对方回复
            timeout: 等待超时（秒）

        Returns:
            对方回复的 C2CMessage，或 None
        """
        if not self._connected:
            logger.error("未连接到 Relay")
            return None

        msg = {
            "type": "message",
            "data": {
                "to": peer_lobster_id,
                "c2c_message": c2c_message.model_dump(),
            }
        }

        if wait_response:
            # 创建 Future 等待回复
            response_key = f"response_{c2c_message.message_id}"
            response_future = asyncio.get_running_loop().create_future()
            self._pending_responses[response_key] = response_future

        await self._send(msg)

        if wait_response:
            try:
                result = await asyncio.wait_for(response_future, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                logger.warning(f"等待回复超时: {c2c_message.message_id}")
                return None
            finally:
                self._pending_responses.pop(response_key, None)

        return None

    async def _send(self, msg: dict):
        """发送消息到 Relay"""
        if self._ws:
            try:
                await self._ws.send(json.dumps(msg, ensure_ascii=False))
            except Exception as e:
                logger.error(f"发送失败: {e}")
                self._connected = False

    async def _listen(self):
        """监听 Relay 消息"""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                    await self._dispatch(msg)
                except json.JSONDecodeError:
                    logger.warning("收到无效 JSON")
                except Exception as e:
                    logger.error(f"消息处理异常: {type(e).__name__}: {e}")
        except Exception as e:
            logger.warning(f"Relay 连接断开: {e}")
            self._connected = False
            # 自动重连
            if self._running:
                asyncio.create_task(self._reconnect())

    async def _reconnect(self):
        """重连 Relay"""
        # 先取消可能仍在运行的旧任务，防止重复并行
        for task_attr in ("_heartbeat_task", "_listener_task"):
            old_task = getattr(self, task_attr, None)
            if old_task and not old_task.done():
                old_task.cancel()

        delay = self._reconnect_delay
        while self._running and not self._connected:
            logger.info(f"🔄 {delay}秒后重连 Relay...")
            await asyncio.sleep(delay)
            try:
                import websockets
                self._ws = await websockets.connect(
                    self.relay_url,
                    ping_interval=30,
                    ping_timeout=60,
                )
                self._connected = True
                delay = self._reconnect_delay

                # 重新注册
                if await self._register():
                    self._listener_task = asyncio.create_task(self._listen())
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    logger.info("✅ 重连成功!")
                    return
            except Exception as e:
                logger.warning(f"重连失败: {e}")
                delay = min(delay * 2, self._max_reconnect_delay)

    async def _dispatch(self, msg: dict):
        """分发 Relay 消息（v2 协议事件）"""
        msg_type = msg.get("type", "")
        msg_data = msg.get("data", {})

        if msg_type == "friend_added":
            # 加好友成功（v2，替代旧的 paired）
            await self._handle_friend_added(msg_data)

        elif msg_type == "friend_request":
            # v0.9: 收到好友申请（需确认/拒绝）
            await self._handle_friend_request(msg_data)

        elif msg_type == "friend_request_sent":
            # v0.9: 好友申请已发送（回复给请求方）
            pair_future = self._pending_responses.pop("_pair_result", None)
            if pair_future and not pair_future.done():
                pair_future.set_result(msg_data)
            logger.info(f"📬 {msg_data.get('message', '好友申请已发送')}")

        elif msg_type == "friend_request_result":
            # v0.9: 好友申请结果（被拒绝/过期/操作确认）
            success = msg_data.get("success", False)
            message = msg_data.get("message", "")
            if success:
                logger.info(f"✅ {message}")
            else:
                logger.warning(f"❌ {message}")
            # 如果有等待中的 pair_future（申请被拒时也要通知）
            pair_future = self._pending_responses.pop("_pair_result", None)
            if pair_future and not pair_future.done():
                pair_future.set_result(None)

        elif msg_type == "pending_requests_list":
            # v0.9: 待处理好友申请列表
            fut = self._pending_responses.pop("_pending_requests_result", None)
            if fut and not fut.done():
                fut.set_result(msg_data.get("requests", []))

        elif msg_type == "pair_failed":
            # 加好友失败
            pair_future = self._pending_responses.pop("_pair_result", None)
            if pair_future and not pair_future.done():
                pair_future.set_result(None)
            logger.warning(f"加好友失败: {msg_data.get('message', '')}")

        elif msg_type == "friend_online":
            # 好友上线（v2 新增）
            if self._on_friend_online:
                await self._on_friend_online(msg_data)

        elif msg_type == "friend_offline":
            # 好友下线（v2，替代旧的 peer_disconnected）
            if self._on_friend_offline:
                await self._on_friend_offline(msg_data)

        elif msg_type == "relayed_message":
            # 收到中继消息
            await self._handle_relayed_message(msg_data)

        elif msg_type == "relayed_response":
            # 收到中继回复
            await self._handle_relayed_response(msg_data)

        elif msg_type == "delivery_failed":
            # 投递失败
            reason = msg_data.get("reason", "")
            logger.warning(f"消息投递失败: {reason}")
            # 如果有等待中的 response Future，通知失败
            to_id = msg_data.get("to", "")
            for key, fut in list(self._pending_responses.items()):
                if key.startswith("response_") and not fut.done():
                    # 不取消——让超时自然处理
                    pass

        elif msg_type == "friends_list":
            # 好友列表响应
            logger.info(f"好友列表: {msg_data.get('friends', [])}")

        elif msg_type == "discover_result":
            # v0.7: 龙虾发现结果
            await self._handle_discover_result(msg_data)

        elif msg_type == "introduction":
            # v0.7: 收到好友引荐
            await self._handle_introduction(msg_data)

        elif msg_type == "introduce_sent":
            # v0.7: 引荐已发送确认
            logger.info(f"引荐已发送: {msg_data.get('message', '')}")

        elif msg_type == "introduce_failed":
            # v0.7: 引荐失败
            logger.warning(f"引荐失败: {msg_data.get('message', '')}")

        elif msg_type == "heartbeat_ack":
            pass

        elif msg_type == "error":
            logger.error(f"Relay 错误: {msg_data.get('message', '')}")

    async def _handle_friend_request(self, data: dict):
        """
        v0.9: 处理收到的好友申请（需用户确认）

        data: {request_id, lobster_id, lobster_name, owner_name, message}
        """
        request_id = data.get("request_id", "")
        owner_name = data.get("owner_name", "未知")
        lobster_name = data.get("lobster_name", "")

        logger.info(f"📬 收到好友申请! {owner_name} 的 {lobster_name} 想加你为好友")

        # 缓存到本地
        self._pending_friend_requests[request_id] = data

        # 回调通知上层（Terminal/SDK）
        if self._on_friend_request:
            await self._on_friend_request(data)

    async def _handle_friend_added(self, data: dict):
        """
        处理加好友成功（v2 协议）

        与旧的 _handle_paired 不同：
        - 使用 lobster_id / lobster_name / owner_name 字段
        - 自动保存到本地通讯录
        - 加好友即信任（无需手动信任）
        """
        friend_lobster_id = data.get("lobster_id", "")
        friend_lobster_name = data.get("lobster_name", "")
        friend_owner_name = data.get("owner_name", "")

        logger.info(
            f"🦞🤝🦞 加好友成功! {friend_lobster_name} (主人: {friend_owner_name})"
        )

        # 注册到本地通讯录（标记为通过 Relay 连接，自动信任）
        peer_info = PeerInfo(
            lobster_id=friend_lobster_id,
            lobster_name=friend_lobster_name,
            owner_name=friend_owner_name,
            endpoint=f"relay://{friend_lobster_id}",  # 走 relay 路由
            trusted=True,  # 通过加好友码互加，自动信任
            last_seen=None,
        )
        self.peers.add_peer(peer_info)

        # 回调
        if self._on_friend_added:
            await self._on_friend_added(data)

        # 解除加好友等待（如果是主动加的）
        pair_future = self._pending_responses.pop("_pair_result", None)
        if pair_future and not pair_future.done():
            pair_future.set_result(data)

    async def _handle_discover_result(self, data: dict):
        """
        v0.7: 处理龙虾发现结果

        data: {matches: [{lobster_id, lobster_name, owner_name, handle, tags}, ...], total_online: N}
        """
        matches = data.get("matches", [])
        total = data.get("total_online", 0)

        logger.info(f"🔍 发现结果: {len(matches)} 只龙虾 (共 {total} 只在线)")

        if self._on_discover_result:
            await self._on_discover_result(data)

    async def _handle_introduction(self, data: dict):
        """
        v0.7: 处理好友引荐通知

        data: {
            from_lobster_id, from_lobster_name, from_owner_name,
            introduced_peer: {lobster_id, lobster_name, owner_name, tags},
            reason, message
        }
        """
        introducer = data.get("from_owner_name", "")
        introduced = data.get("introduced_peer", {})

        logger.info(
            f"🦞🤝 收到引荐! {introducer} 推荐了 {introduced.get('owner_name', '未知')}"
        )

        # 注册被引荐的龙虾到本地通讯录（低信任，标记来源）
        if introduced.get("lobster_id"):
            existing = self.peers.get_peer(introduced["lobster_id"])
            if not existing:
                peer_info = PeerInfo(
                    lobster_id=introduced["lobster_id"],
                    lobster_name=introduced.get("lobster_name", ""),
                    owner_name=introduced.get("owner_name", ""),
                    endpoint=f"relay://{introduced['lobster_id']}",
                    tags=introduced.get("tags", []),
                    trust_score=5,  # 引荐初始信任分
                    introduced_by=data.get("from_lobster_id", ""),
                )
                self.peers.add_peer(peer_info)

        if self._on_introduction:
            await self._on_introduction(data)

    async def _handle_relayed_message(self, data: dict):
        """处理通过 Relay 转发来的消息"""
        from_lobster_id = data.get("from_lobster_id", "")
        c2c_data = data.get("c2c_message", {})

        try:
            incoming = C2CMessage(**c2c_data)
        except Exception as e:
            logger.error(f"解析 C2C 消息失败: {e}")
            return

        logger.info(
            f"🦞←🦞 (Relay) 收到 {incoming.msg_type} from "
            f"{incoming.from_lobster_name} ({incoming.from_owner_name})"
        )

        # 更新通讯录 last_seen
        peer = self.peers.get_peer(from_lobster_id)
        if peer:
            from datetime import datetime
            peer.last_seen = datetime.now().isoformat()

        # 调用消息处理回调
        reply = None
        if self._on_message:
            reply = await self._on_message(incoming)

        # 如果有回复，通过 Relay 发回去
        if reply:
            await self._send({
                "type": "relay_response",
                "data": {
                    "to": from_lobster_id,
                    "c2c_message": reply.model_dump(),
                }
            })

    async def _handle_relayed_response(self, data: dict):
        """处理通过 Relay 转发来的回复"""
        c2c_data = data.get("c2c_message", {})

        try:
            reply = C2CMessage(**c2c_data)
        except Exception as e:
            logger.error(f"解析回复消息失败: {e}")
            return

        # 查找对应的等待 Future
        reply_to = reply.reply_to
        if reply_to:
            response_key = f"response_{reply_to}"
            fut = self._pending_responses.pop(response_key, None)
            if fut and not fut.done():
                fut.set_result(reply)
                return

        # 没有匹配的等待，作为普通消息处理
        if self._on_message:
            await self._on_message(reply)

    async def _heartbeat_loop(self):
        """定期心跳"""
        try:
            while self._running and self._connected:
                await asyncio.sleep(25)
                if self._connected:
                    await self._send({"type": "heartbeat", "data": {}})
        except asyncio.CancelledError:
            pass
