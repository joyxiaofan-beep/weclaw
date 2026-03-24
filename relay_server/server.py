"""
WeClaw Relay Server — 龙虾中继服务器 v2

一个极简 WebSocket 中继服务器，让龙虾间零成本互联。

核心理念：
- **平台成本最低** — Relay 只做路由和转发，不存储消息，不调用 AI
- **持久身份（龙虾号）** — 每只龙虾有唯一的龙虾号（如 lobster_a3f8），重启不变
- **临时配对码仅用于加好友** — 像微信"面对面加好友"，加完一次后续靠龙虾号直连
- **好友关系由客户端维护** — Relay 不存储好友关系，只在线时路由

架构：
    用户的 AI → 用户本地运行（自带 API Key，Relay 不管）
    Relay Server → 只做 3 件事：
      1. 龙虾号注册（上线时报到）
      2. 临时配对码加好友（一次性，加完即弃）
      3. 按龙虾号转发消息（两端都在线时透传）

部署：
    pip install websockets
    python -m relay_server.server
    # 或者用 Docker / fly.io（一台 $5 机器扛上千龙虾）

环境变量：
    RELAY_HOST: 监听地址 (默认 0.0.0.0)
    RELAY_PORT: 监听端口 (默认 8900)
    RELAY_MAX_LOBSTERS: 最大同时在线龙虾数 (默认 2000)
"""

import asyncio
import json
import logging
import os
import random
import string
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("❌ 需要安装 websockets: pip install websockets")
    raise

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("relay")

# ──────────────────────────────────────────
# 配置
# ──────────────────────────────────────────

RELAY_HOST = os.environ.get("RELAY_HOST", "0.0.0.0")
RELAY_PORT = int(os.environ.get("RELAY_PORT", "8900"))
MAX_LOBSTERS = int(os.environ.get("RELAY_MAX_LOBSTERS", "2000"))

# 临时配对码有效期（秒）— 仅加好友用，加完即弃
PAIR_CODE_TTL = 10 * 60  # 10 分钟（更短，因为只用一次）

# 心跳间隔
HEARTBEAT_INTERVAL = 30  # 秒


# ──────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────

@dataclass
class OnlineLobster:
    """一只在线的龙虾（仅运行时状态，不持久化）"""
    lobster_id: str       # 龙虾号（持久，客户端生成并保存）
    lobster_name: str     # 显示名
    owner_name: str       # 主人名
    ws: object            # WebSocket 连接
    pair_code: str = ""   # 临时加好友码（一次性）
    tags: list = field(default_factory=list)  # v0.7: 能力标签，用于发现
    handle: str = ""      # v0.7: 可读别名
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    # 好友列表（由客户端 register 时上报，Relay 用于鉴权转发）
    friends: set = field(default_factory=set)
    # v0.8: enriched profile for discovery
    description: str = ""
    services_offered: list = field(default_factory=list)
    interests: list = field(default_factory=list)
    industries: list = field(default_factory=list)
    location_area: str = ""


class RelayServer:
    """
    极简 WebSocket 中继服务器 v2

    只做三件事：注册、加好友、转发。
    不存储任何持久数据，不调用任何 AI。

    协议消息格式 (JSON):
    {
        "type": "register" | "pair" | "message" | "heartbeat" | ...,
        "data": { ... }
    }
    """

    def __init__(self):
        # lobster_id -> OnlineLobster（在线状态）
        self._online: dict[str, OnlineLobster] = {}
        # pair_code -> (lobster_id, created_at)（临时加好友码）
        self._pair_codes: dict[str, tuple[str, float]] = {}
        # ws_id -> lobster_id（反向索引）
        self._ws_to_id: dict[int, str] = {}

    def _generate_pair_code(self) -> str:
        """
        生成临时加好友码: #XXXX

        短、好记、只用一次。类似微信面对面加好友的 4 位数。
        """
        while True:
            code = "#" + "".join(random.choices(string.digits, k=4))
            if code not in self._pair_codes:
                return code

    async def _cleanup_expired_codes(self):
        """定期清理过期的临时加好友码"""
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [
                code for code, (_, ts) in self._pair_codes.items()
                if now - ts > PAIR_CODE_TTL
            ]
            for code in expired:
                del self._pair_codes[code]
            if expired:
                logger.info(f"🧹 清理了 {len(expired)} 个过期加好友码")

    async def _cleanup_stale_connections(self):
        """定期清理断开的连接"""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL * 3)
            now = time.time()
            stale = [
                lid for lid, lobster in self._online.items()
                if now - lobster.last_heartbeat > HEARTBEAT_INTERVAL * 3
            ]
            for lid in stale:
                logger.info(f"🧹 清理超时龙虾: {lid}")
                await self._remove_lobster(lid)

    async def _remove_lobster(self, lobster_id: str):
        """移除一只龙虾（下线）"""
        lobster = self._online.pop(lobster_id, None)
        if lobster:
            # 清理反向索引
            self._ws_to_id.pop(id(lobster.ws), None)
            # 清理加好友码
            codes_to_remove = [
                c for c, (lid, _) in self._pair_codes.items() if lid == lobster_id
            ]
            for c in codes_to_remove:
                del self._pair_codes[c]
            # 通知好友
            for friend_id in lobster.friends:
                friend = self._online.get(friend_id)
                if friend:
                    asyncio.create_task(self._send_to(friend.ws, {
                        "type": "friend_offline",
                        "data": {
                            "lobster_id": lobster_id,
                            "lobster_name": lobster.lobster_name,
                            "owner_name": lobster.owner_name,
                        }
                    }))
            logger.info(
                f"🦞❌ {lobster.lobster_name}[{lobster_id}] 已离线 "
                f"[在线: {len(self._online)}]"
            )

    async def _send_to(self, ws, message: dict):
        """安全地发送消息"""
        try:
            await ws.send(json.dumps(message, ensure_ascii=False))
        except Exception:
            pass

    # ──────────────────────────────────────────
    # 协议处理
    # ──────────────────────────────────────────

    async def _handle_register(self, ws, data: dict) -> dict:
        """
        龙虾上线注册

        data: {
            lobster_id: "lobster_a3f8",    # 持久龙虾号（客户端生成）
            lobster_name: "🦞 小虾",
            owner_name: "Alice",
            friends: ["lobster_b2c1", ...]  # 已有好友列表（客户端上报）
        }
        返回: {pair_code, lobster_id, online_friends: [...]}
        """
        if len(self._online) >= MAX_LOBSTERS:
            return {"type": "error", "data": {"message": "服务器已满，请稍后再试"}}

        lobster_id = data.get("lobster_id", "")
        if not lobster_id:
            return {"type": "error", "data": {"message": "缺少 lobster_id"}}

        # 重连处理
        if lobster_id in self._online:
            old = self._online[lobster_id]
            self._ws_to_id.pop(id(old.ws), None)
            # 清理旧的加好友码
            old_codes = [c for c, (lid, _) in self._pair_codes.items() if lid == lobster_id]
            for c in old_codes:
                del self._pair_codes[c]

        # 生成临时加好友码
        pair_code = self._generate_pair_code()

        # 解析好友列表
        friends = set(data.get("friends", []))

        lobster = OnlineLobster(
            lobster_id=lobster_id,
            lobster_name=data.get("lobster_name", "🦞 未命名"),
            owner_name=data.get("owner_name", "匿名"),
            ws=ws,
            pair_code=pair_code,
            tags=data.get("tags", []),      # v0.7: 能力标签
            handle=data.get("handle", ""),   # v0.7: 可读别名
            friends=friends,
            # v0.8: enriched profile
            description=data.get("description", ""),
            services_offered=data.get("services_offered", []),
            interests=data.get("interests", []),
            industries=data.get("industries", []),
            location_area=data.get("location_area", ""),
        )

        self._online[lobster_id] = lobster
        self._pair_codes[pair_code] = (lobster_id, time.time())
        self._ws_to_id[id(ws)] = lobster_id

        # 查一下哪些好友在线
        online_friends = []
        for friend_id in friends:
            friend = self._online.get(friend_id)
            if friend:
                online_friends.append({
                    "lobster_id": friend.lobster_id,
                    "lobster_name": friend.lobster_name,
                    "owner_name": friend.owner_name,
                })
                # 通知好友"我上线了"
                asyncio.create_task(self._send_to(friend.ws, {
                    "type": "friend_online",
                    "data": {
                        "lobster_id": lobster_id,
                        "lobster_name": lobster.lobster_name,
                        "owner_name": lobster.owner_name,
                    }
                }))

        logger.info(
            f"🦞✅ {lobster.lobster_name}[{lobster_id}] 已上线 "
            f"[加好友码: {pair_code}] [好友: {len(friends)}] [在线: {len(self._online)}]"
        )

        return {
            "type": "registered",
            "data": {
                "pair_code": pair_code,
                "lobster_id": lobster_id,
                "online_friends": online_friends,
                "message": f"上线成功！加好友码: {pair_code}",
            }
        }

    async def _handle_pair(self, ws, data: dict) -> dict:
        """
        临时配对码加好友（仅首次，加完双方本地保存好友关系）

        data: {pair_code: "#1234"}
        返回: 双方互相通知
        """
        pair_code = data.get("pair_code", "").strip()

        # 找到请求方
        requester_id = self._ws_to_id.get(id(ws))
        if not requester_id or requester_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        requester = self._online[requester_id]

        # 查找加好友码
        code_info = self._pair_codes.get(pair_code)
        if not code_info:
            return {
                "type": "pair_failed",
                "data": {"message": f"加好友码 {pair_code} 无效或已过期"}
            }

        target_id, _ = code_info
        if target_id not in self._online:
            return {
                "type": "pair_failed",
                "data": {"message": "对方龙虾已离线"}
            }

        target = self._online[target_id]

        # 不能加自己
        if target_id == requester_id:
            return {"type": "pair_failed", "data": {"message": "不能加自己为好友"}}

        # 已经是好友了
        if target_id in requester.friends:
            return {
                "type": "pair_failed",
                "data": {"message": f"你们已经是好友了！直接用「龙虾传话 {target.owner_name} 内容」传话吧"}
            }

        # 双向加好友（运行时状态）
        requester.friends.add(target_id)
        target.friends.add(requester_id)

        # 用完即弃：删除加好友码（一次性）
        del self._pair_codes[pair_code]

        logger.info(
            f"🦞🤝🦞 加好友成功! {requester.lobster_name}[{requester_id}] "
            f"↔ {target.lobster_name}[{target_id}]"
        )

        # 通知目标（被加的一方）
        await self._send_to(target.ws, {
            "type": "friend_added",
            "data": {
                "lobster_id": requester.lobster_id,
                "lobster_name": requester.lobster_name,
                "owner_name": requester.owner_name,
                "message": f"{requester.owner_name} 加你为好友了！",
            }
        })

        # 返回给请求方
        return {
            "type": "friend_added",
            "data": {
                "lobster_id": target.lobster_id,
                "lobster_name": target.lobster_name,
                "owner_name": target.owner_name,
                "message": f"已和 {target.owner_name} 成为好友！",
            }
        }

    async def _handle_message(self, ws, data: dict) -> Optional[dict]:
        """
        好友间消息转发（通过龙虾号路由）

        data: {to: "lobster_b2c1", c2c_message: {...}}
        安全检查: 只有好友才能互发消息
        """
        sender_id = self._ws_to_id.get(id(ws))
        if not sender_id or sender_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        sender = self._online[sender_id]
        target_id = data.get("to", "") or data.get("to_lobster_id", "")
        c2c_message = data.get("c2c_message", {})

        if not target_id:
            return {"type": "error", "data": {"message": "缺少目标龙虾号"}}

        # 检查是否是好友
        if target_id not in sender.friends:
            return {
                "type": "delivery_failed",
                "data": {
                    "to": target_id,
                    "reason": "对方不是你的好友，请先通过加好友码添加",
                }
            }

        # 检查是否在线
        target = self._online.get(target_id)
        if not target:
            return {
                "type": "delivery_failed",
                "data": {
                    "to": target_id,
                    "reason": "好友不在线（离线消息功能暂未支持）",
                }
            }

        # 转发
        await self._send_to(target.ws, {
            "type": "relayed_message",
            "data": {
                "from_lobster_id": sender_id,
                "c2c_message": c2c_message,
            }
        })

        logger.debug(
            f"📨 {sender.lobster_name} → {target.lobster_name} "
            f"({c2c_message.get('msg_type', '?')})"
        )

        return {
            "type": "delivered",
            "data": {
                "to": target_id,
                "message_id": c2c_message.get("message_id", ""),
            }
        }

    async def _handle_relay_response(self, ws, data: dict) -> Optional[dict]:
        """处理好友回复（relay 回复消息）"""
        sender_id = self._ws_to_id.get(id(ws))
        if not sender_id or sender_id not in self._online:
            return None

        target_id = data.get("to", "") or data.get("to_lobster_id", "")
        c2c_message = data.get("c2c_message", {})

        target = self._online.get(target_id)
        if not target:
            return None

        await self._send_to(target.ws, {
            "type": "relayed_response",
            "data": {
                "from_lobster_id": sender_id,
                "c2c_message": c2c_message,
            }
        })
        return None

    async def _handle_heartbeat(self, ws, data: dict) -> dict:
        """心跳"""
        lobster_id = self._ws_to_id.get(id(ws))
        if lobster_id and lobster_id in self._online:
            self._online[lobster_id].last_heartbeat = time.time()
        return {"type": "heartbeat_ack", "data": {}}

    async def _handle_list_friends(self, ws, data: dict) -> dict:
        """查看在线好友"""
        lobster_id = self._ws_to_id.get(id(ws))
        if not lobster_id or lobster_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        lobster = self._online[lobster_id]
        friends = []
        for friend_id in lobster.friends:
            friend = self._online.get(friend_id)
            online = friend is not None
            friends.append({
                "lobster_id": friend_id,
                "lobster_name": friend.lobster_name if friend else "",
                "owner_name": friend.owner_name if friend else "",
                "online": online,
            })

        return {
            "type": "friends_list",
            "data": {"friends": friends}
        }

    async def _handle_discover(self, ws, data: dict) -> dict:
        """
        v0.7: 龙虾发现 — 按标签搜索在线龙虾（借鉴 Tobira.ai 全局发现/匹配）

        data: {tags: ["AI", "设计"], limit: 10}
        返回: 匹配的在线龙虾摘要列表（不含好友，已经认识的不推荐）

        隐私保护：
        - 只返回摘要信息（不含 ws 连接、endpoint）
        - 不暴露好友列表
        - 请求方需已注册
        """
        requester_id = self._ws_to_id.get(id(ws))
        if not requester_id or requester_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        requester = self._online[requester_id]
        search_tags = set(t.lower() for t in data.get("tags", []))
        limit = min(data.get("limit", 10), 50)  # 最多返回 50 个

        results = []
        for lid, lobster in self._online.items():
            # 跳过自己
            if lid == requester_id:
                continue
            # 跳过已是好友的
            if lid in requester.friends:
                continue
            # 标签匹配（如果指定了标签）
            if search_tags:
                lobster_tags = set(t.lower() for t in lobster.tags)
                if not search_tags & lobster_tags:
                    continue

            results.append({
                "lobster_id": lobster.lobster_id,
                "lobster_name": lobster.lobster_name,
                "owner_name": lobster.owner_name,
                "handle": lobster.handle,
                "tags": lobster.tags,
                # v0.8: enriched profile for discovery
                "description": lobster.description,
                "services_offered": lobster.services_offered,
                "interests": lobster.interests,
                "industries": lobster.industries,
                "location_area": lobster.location_area,
            })

            if len(results) >= limit:
                break

        logger.info(
            f"🔍 {requester.lobster_name} 搜索龙虾 "
            f"[tags: {search_tags or '全部'}] → 找到 {len(results)} 只"
        )

        return {
            "type": "discover_result",
            "data": {
                "matches": results,
                "total_online": len(self._online) - 1,  # 不含自己
                "message": f"找到 {len(results)} 只匹配的龙虾",
            }
        }

    async def _handle_introduce(self, ws, data: dict) -> dict:
        """
        v0.7: 好友引荐 — A 向 B 介绍 C（借鉴 Tobira.ai 的匹配推荐机制）

        data: {
            target_id: "lobster_xxx",      # 被介绍对象 C
            introduce_to_id: "lobster_yyy", # 介绍给谁 B
            reason: "他在 AI 领域很厉害"     # 引荐理由
        }
        安全检查: A 必须同时是 B 和 C 的好友
        """
        introducer_id = self._ws_to_id.get(id(ws))
        if not introducer_id or introducer_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        introducer = self._online[introducer_id]
        target_id = data.get("target_id", "")
        introduce_to_id = data.get("introduce_to_id", "")
        reason = data.get("reason", "")

        if not target_id or not introduce_to_id:
            return {"type": "error", "data": {"message": "缺少目标龙虾号"}}

        # 安全检查：引荐人必须是双方的好友
        if target_id not in introducer.friends:
            return {
                "type": "introduce_failed",
                "data": {"message": f"你还不是 {target_id} 的好友，无法引荐"}
            }
        if introduce_to_id not in introducer.friends:
            return {
                "type": "introduce_failed",
                "data": {"message": f"你还不是 {introduce_to_id} 的好友，无法引荐"}
            }

        # 双方已经是好友了
        target = self._online.get(target_id)
        introduce_to = self._online.get(introduce_to_id)

        if target and introduce_to_id in (target.friends if target else set()):
            return {
                "type": "introduce_failed",
                "data": {"message": "他们已经是好友了，不需要引荐"}
            }

        # 向被介绍给的人 B 发送引荐通知
        if introduce_to:
            target_info = {
                "lobster_id": target_id,
                "lobster_name": target.lobster_name if target else "",
                "owner_name": target.owner_name if target else "",
                "tags": target.tags if target else [],
            }
            await self._send_to(introduce_to.ws, {
                "type": "introduction",
                "data": {
                    "from_lobster_id": introducer_id,
                    "from_lobster_name": introducer.lobster_name,
                    "from_owner_name": introducer.owner_name,
                    "introduced_peer": target_info,
                    "reason": reason,
                    "message": f"{introducer.owner_name} 向你推荐了 {target_info.get('owner_name', '一只龙虾')}",
                }
            })

        # 同时通知被引荐的人 C
        if target:
            introduce_to_info = {
                "lobster_id": introduce_to_id,
                "lobster_name": introduce_to.lobster_name if introduce_to else "",
                "owner_name": introduce_to.owner_name if introduce_to else "",
                "tags": introduce_to.tags if introduce_to else [],
            }
            await self._send_to(target.ws, {
                "type": "introduction",
                "data": {
                    "from_lobster_id": introducer_id,
                    "from_lobster_name": introducer.lobster_name,
                    "from_owner_name": introducer.owner_name,
                    "introduced_peer": introduce_to_info,
                    "reason": reason,
                    "message": f"{introducer.owner_name} 把你介绍给了 {introduce_to_info.get('owner_name', '一只龙虾')}",
                }
            })

        logger.info(
            f"🦞🤝🦞 引荐! {introducer.lobster_name} 介绍 "
            f"{target_id} ↔ {introduce_to_id} (理由: {reason or '无'})"
        )

        return {
            "type": "introduce_sent",
            "data": {
                "target_id": target_id,
                "introduce_to_id": introduce_to_id,
                "message": "引荐已发送！双方会收到通知。",
            }
        }

    # ──────────────────────────────────────────
    # WebSocket 连接处理
    # ──────────────────────────────────────────

    async def handle_connection(self, ws):
        """处理一个 WebSocket 连接"""
        try:
            async for raw_message in ws:
                try:
                    msg = json.loads(raw_message)
                except json.JSONDecodeError:
                    await self._send_to(ws, {
                        "type": "error",
                        "data": {"message": "无效的 JSON"}
                    })
                    continue

                msg_type = msg.get("type", "")
                msg_data = msg.get("data", {})

                handler = {
                    "register": self._handle_register,
                    "pair": self._handle_pair,
                    "message": self._handle_message,
                    "relay_response": self._handle_relay_response,
                    "heartbeat": self._handle_heartbeat,
                    "list_friends": self._handle_list_friends,
                    "discover": self._handle_discover,       # v0.7: 龙虾发现
                    "introduce": self._handle_introduce,     # v0.7: 好友引荐
                }.get(msg_type)

                if handler:
                    reply = await handler(ws, msg_data)
                    if reply:
                        await self._send_to(ws, reply)
                else:
                    await self._send_to(ws, {
                        "type": "error",
                        "data": {"message": f"未知消息类型: {msg_type}"}
                    })

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"连接处理异常: {type(e).__name__}: {e}")
        finally:
            lid = self._ws_to_id.pop(id(ws), None)
            if lid:
                await self._remove_lobster(lid)

    async def run(self):
        """启动 Relay Server"""
        logger.info("🦞🌐 WeClaw Relay Server v2.1 启动中...")
        logger.info(f"   📡 地址: ws://{RELAY_HOST}:{RELAY_PORT}")
        logger.info(f"   🦞 最大在线: {MAX_LOBSTERS}")
        logger.info(f"   🔍 发现协议: 已启用 (v0.7)")
        logger.info(f"   💰 平台成本: 0（只做路由，AI 走用户自己的 Key）")

        asyncio.create_task(self._cleanup_expired_codes())
        asyncio.create_task(self._cleanup_stale_connections())

        async with serve(
            self.handle_connection,
            RELAY_HOST,
            RELAY_PORT,
            ping_interval=HEARTBEAT_INTERVAL,
            ping_timeout=HEARTBEAT_INTERVAL * 2,
        ):
            logger.info("✅ Relay Server 已就绪，等待龙虾连接...")
            await asyncio.Future()  # 永远运行


def main():
    server = RelayServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("🛑 Relay Server 已关闭")


if __name__ == "__main__":
    main()
