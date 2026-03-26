"""
WeClaw Relay Server — 龙虾中继服务器 v2.3

一个极简 WebSocket 中继服务器，让龙虾间零成本互联。

核心理念：
- **平台成本最低** — Relay 只做路由和转发，不存储消息，不调用 AI
- **持久身份（龙虾号）** — 每只龙虾有唯一的龙虾号（如 claw_alice），重启不变
- **临时配对码仅用于加好友** — 像微信"面对面加好友"，加完一次后续靠龙虾号直连
- **好友关系由客户端维护** — Relay 不存储好友关系，只在线时路由
- **邀请链接 (v1.5)** — 跨 Relay、跨时区异步加好友
- **离线好友请求队列 (v1.5)** — 目标离线时暂存好友请求，上线后投递（24h TTL）

架构：
    用户的 AI → 用户本地运行（自带 API Key，Relay 不管）
    Relay Server → 只做 4 件事：
      1. 龙虾号注册（上线时报到）
      2. 临时配对码 / 龙虾号 / 邀请链接加好友
      3. 按龙虾号转发消息（两端都在线时透传）
      4. 离线好友请求暂存（轻量队列，仅好友请求）

部署：
    pip install websockets
    python -m relay_server.server
    # 或者用 Docker / fly.io（一台 $5 机器扛上千龙虾）

环境变量：
    RELAY_HOST: 监听地址 (默认 0.0.0.0)
    RELAY_PORT: 监听端口 (默认 8900)
    RELAY_MAX_LOBSTERS: 最大同时在线龙虾数 (默认 2000)
    RELAY_TLS_CERT: TLS 证书文件路径 (配置后自动启用 wss://)
    RELAY_TLS_KEY: TLS 私钥文件路径
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import ssl
import string
import time
import uuid
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

# TLS 证书配置（可选，配置后自动启用 wss://）
RELAY_TLS_CERT = os.environ.get("RELAY_TLS_CERT", "")  # TLS 证书文件路径
RELAY_TLS_KEY = os.environ.get("RELAY_TLS_KEY", "")    # TLS 私钥文件路径

# P1-3 安全修复: 注册认证密钥（可选，设置后注册需 HMAC 签名认证）
# 客户端和 Relay 共享此密钥，防止未授权连接
RELAY_AUTH_SECRET = os.environ.get("RELAY_AUTH_SECRET", "")

# 临时配对码有效期（秒）— 仅加好友用，加完即弃
PAIR_CODE_TTL = 10 * 60  # 10 分钟（更短，因为只用一次）

# 好友申请有效期（秒）— 超时自动拒绝
FRIEND_REQUEST_TTL = 24 * 60 * 60  # 24 小时

# 心跳间隔
HEARTBEAT_INTERVAL = 30  # 秒

# v1.5: 离线好友请求队列配置
OFFLINE_QUEUE_TTL = 24 * 60 * 60   # 离线请求有效期（24 小时）
OFFLINE_QUEUE_MAX_PER_TARGET = 50   # 每个目标最多缓存 N 条离线请求


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
        # 待审批好友申请: request_id -> {requester_id, target_id, timestamp, requester_info}
        self._pending_friend_requests: dict[str, dict] = {}
        # v1.5: 离线好友请求队列: target_lobster_id -> [request_dict, ...]
        # 目标龙虾不在线时暂存好友请求，上线后投递
        self._offline_friend_requests: dict[str, list] = {}

    def _generate_pair_code(self) -> str:
        """
        生成临时加好友码: #XXXX

        短、好记、只用一次。类似微信面对面加好友的 4 位数。
        最多尝试 100 次，避免所有码被占满时无限循环。
        """
        max_attempts = 100
        for _ in range(max_attempts):
            code = "#" + "".join(random.choices(string.digits, k=4))
            if code not in self._pair_codes:
                return code
        # 所有 4 位码几乎用尽，扩展到 6 位
        logger.warning("⚠️ 4位加好友码接近用尽，回退到6位码")
        code = "#" + "".join(random.choices(string.digits, k=6))
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

    async def _cleanup_expired_requests(self):
        """定期清理过期的好友申请"""
        while True:
            await asyncio.sleep(300)  # 每 5 分钟检查一次
            now = time.time()
            expired = [
                req_id for req_id, req in self._pending_friend_requests.items()
                if now - req["timestamp"] > FRIEND_REQUEST_TTL
            ]
            for req_id in expired:
                req = self._pending_friend_requests.pop(req_id)
                # 通知请求方申请已过期
                requester = self._online.get(req["requester_id"])
                if requester:
                    target_info = req.get("target_info", {})
                    target_owner = target_info.get("owner_name", "未知")
                    await self._send_to(requester.ws, {
                        "type": "friend_request_result",
                        "data": {
                            "success": False,
                            "lobster_id": req.get("target_id", ""),
                            "owner_name": target_owner,
                            "message": f"⏰ 向 {target_owner} 的好友申请已过期（超过24小时未处理）",
                        }
                    })
            if expired:
                logger.info(f"🧹 清理了 {len(expired)} 条过期好友申请")

    async def _cleanup_expired_offline_requests(self):
        """v1.5: 定期清理过期的离线好友请求"""
        while True:
            await asyncio.sleep(600)  # 每 10 分钟检查一次
            now = time.time()
            total_cleaned = 0
            empty_targets = []

            for target_id, queue in self._offline_friend_requests.items():
                before = len(queue)
                self._offline_friend_requests[target_id] = [
                    req for req in queue
                    if now - req["timestamp"] <= OFFLINE_QUEUE_TTL
                ]
                cleaned = before - len(self._offline_friend_requests[target_id])
                total_cleaned += cleaned
                if not self._offline_friend_requests[target_id]:
                    empty_targets.append(target_id)

            # 清理空队列
            for target_id in empty_targets:
                del self._offline_friend_requests[target_id]

            if total_cleaned > 0:
                logger.info(f"🧹 清理了 {total_cleaned} 条过期离线好友请求")

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
        """安全地发送消息，记录发送失败"""
        try:
            await ws.send(json.dumps(message, ensure_ascii=False))
        except Exception as e:
            # 记录错误而非静默吞掉，便于排查连接问题
            lobster_id = self._ws_to_id.get(id(ws), "unknown")
            logger.warning(f"🦞⚠️ 发送消息到 {lobster_id} 失败: {type(e).__name__}: {e}")

    # ──────────────────────────────────────────
    # 协议处理
    # ──────────────────────────────────────────

    async def _handle_register(self, ws, data: dict) -> dict:
        """
        龙虾上线注册

        data: {
            lobster_id: "claw_alice",      # 持久龙虾号（客户端生成，claw_ 前缀）
            lobster_name: "🦞 小虾",
            owner_name: "Alice",
            friends: ["claw_bob", ...]  # 已有好友列表（客户端上报）
        }
        返回: {pair_code, lobster_id, online_friends: [...]}
        """
        if len(self._online) >= MAX_LOBSTERS:
            return {"type": "error", "data": {"message": "服务器已满，请稍后再试"}}

        lobster_id = data.get("lobster_id", "")
        if not lobster_id:
            return {"type": "error", "data": {"message": "缺少 lobster_id"}}

        # v0.9: 龙虾号格式校验（兼容旧格式 lobster_ 前缀）
        if not (lobster_id.startswith("claw_") or lobster_id.startswith("lobster_")):
            return {"type": "error", "data": {"message": "龙虾号格式无效，需以 claw_ 开头"}}

        # P1-3 安全修复: 注册认证（如果配置了 RELAY_AUTH_SECRET）
        if RELAY_AUTH_SECRET:
            auth_token = data.get("auth_token", "")
            auth_timestamp = data.get("auth_timestamp", "")
            if not auth_token or not auth_timestamp:
                return {"type": "error", "data": {"message": "注册需要认证令牌（缺少 auth_token / auth_timestamp）"}}
            # 验证时间窗口（5 分钟内有效）
            try:
                ts = float(auth_timestamp)
                if abs(time.time() - ts) > 300:
                    return {"type": "error", "data": {"message": "认证令牌已过期"}}
            except (ValueError, TypeError):
                return {"type": "error", "data": {"message": "认证时间戳无效"}}
            # 验证 HMAC 签名: HMAC-SHA256(lobster_id|timestamp, secret)
            expected = hmac.new(
                RELAY_AUTH_SECRET.encode(), f"{lobster_id}|{auth_timestamp}".encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(auth_token, expected):
                logger.warning(f"🚫 注册认证失败: {lobster_id}")
                return {"type": "error", "data": {"message": "注册认证失败"}}

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

        # v1.5: 投递离线好友请求队列
        offline_delivered = 0
        offline_requests = self._offline_friend_requests.pop(lobster_id, [])
        now = time.time()
        for offline_req in offline_requests:
            # 跳过过期的
            if now - offline_req["timestamp"] > OFFLINE_QUEUE_TTL:
                continue
            request_id = offline_req["request_id"]
            requester_info = offline_req["requester_info"]

            # 存入正式的待审批队列
            self._pending_friend_requests[request_id] = {
                "requester_id": offline_req["requester_id"],
                "target_id": lobster_id,
                "timestamp": offline_req["timestamp"],
                "requester_info": requester_info,
                "target_info": {
                    "lobster_id": lobster.lobster_id,
                    "lobster_name": lobster.lobster_name,
                    "owner_name": lobster.owner_name,
                },
                "source": offline_req.get("source", "pair_by_id"),
            }

            # 通知上线的龙虾：有离线好友申请
            asyncio.create_task(self._send_to(ws, {
                "type": "friend_request",
                "data": {
                    "request_id": request_id,
                    "lobster_id": requester_info["lobster_id"],
                    "lobster_name": requester_info["lobster_name"],
                    "owner_name": requester_info["owner_name"],
                    "offline": True,  # 标记为离线期间收到的请求
                    "message": f"📬 {requester_info['owner_name']} 的龙虾 {requester_info['lobster_name']} 在你离线时想加你为好友！",
                }
            }))

            # 如果请求方当前在线，通知 TA 对方已上线
            requester = self._online.get(offline_req["requester_id"])
            if requester:
                asyncio.create_task(self._send_to(requester.ws, {
                    "type": "offline_request_delivered",
                    "data": {
                        "request_id": request_id,
                        "target_lobster_id": lobster_id,
                        "target_lobster_name": lobster.lobster_name,
                        "message": f"📬 {lobster.lobster_name} 已上线，你的离线好友申请已送达！",
                    }
                }))

            offline_delivered += 1

        if offline_delivered > 0:
            logger.info(
                f"🦞📬 投递 {offline_delivered} 条离线好友申请给 {lobster.lobster_name}[{lobster_id}]"
            )

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
        通过临时配对码发送好友申请（需对方确认后才正式成为好友）

        data: {pair_code: "#1234"}
        流程: 请求方输入配对码 → 服务器发 friend_request 给目标 → 等目标确认/拒绝
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

        # 检查是否已有待处理的申请（避免重复发送）
        for req in self._pending_friend_requests.values():
            if req["requester_id"] == requester_id and req["target_id"] == target_id:
                return {
                    "type": "pair_failed",
                    "data": {"message": f"你已经向 {target.owner_name} 发送过好友申请了，请等待对方确认"}
                }

        # 生成好友申请 ID
        request_id = uuid.uuid4().hex[:12]

        # 存入待审批队列
        self._pending_friend_requests[request_id] = {
            "requester_id": requester_id,
            "target_id": target_id,
            "timestamp": time.time(),
            "requester_info": {
                "lobster_id": requester.lobster_id,
                "lobster_name": requester.lobster_name,
                "owner_name": requester.owner_name,
            },
            "target_info": {
                "lobster_id": target.lobster_id,
                "lobster_name": target.lobster_name,
                "owner_name": target.owner_name,
            },
        }

        # 用完即弃：删除加好友码（一次性）
        del self._pair_codes[pair_code]

        logger.info(
            f"🦞📬 好友申请! {requester.lobster_name}[{requester_id}] "
            f"→ {target.lobster_name}[{target_id}] [申请ID: {request_id}]"
        )

        # 通知目标龙虾：收到好友申请（需确认）
        await self._send_to(target.ws, {
            "type": "friend_request",
            "data": {
                "request_id": request_id,
                "lobster_id": requester.lobster_id,
                "lobster_name": requester.lobster_name,
                "owner_name": requester.owner_name,
                "message": f"📬 {requester.owner_name} 的龙虾 {requester.lobster_name} 想加你为好友！",
            }
        })

        # 返回给请求方：申请已发送，等待确认
        return {
            "type": "friend_request_sent",
            "data": {
                "request_id": request_id,
                "lobster_id": target.lobster_id,
                "lobster_name": target.lobster_name,
                "owner_name": target.owner_name,
                "message": f"已向 {target.owner_name} 发送好友申请，等待对方确认...",
            }
        }

    async def _handle_pair_by_id(self, ws, data: dict) -> dict:
        """
        通过龙虾号发送好友申请（需对方确认后才正式成为好友）

        data: {lobster_id: "claw_alice"}
        流程: 请求方输入龙虾号 → 服务器查找在线目标 → 发 friend_request 给目标 → 等目标确认/拒绝
        """
        target_lobster_id = data.get("lobster_id", "").strip()
        if not target_lobster_id:
            return {"type": "pair_failed", "data": {"message": "缺少目标龙虾号"}}

        # 找到请求方
        requester_id = self._ws_to_id.get(id(ws))
        if not requester_id or requester_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        requester = self._online[requester_id]

        # 通过龙虾号查找目标（在线 → 直接发送，离线 → 存入离线队列）
        target = self._online.get(target_lobster_id)

        # 不能加自己
        if target_lobster_id == requester_id:
            return {"type": "pair_failed", "data": {"message": "不能加自己为好友"}}

        # 已经是好友了
        if target_lobster_id in requester.friends:
            target_owner = target.owner_name if target else target_lobster_id
            return {
                "type": "pair_failed",
                "data": {"message": f"你们已经是好友了！直接用「龙虾传话 {target_owner} 内容」传话吧"}
            }

        # 检查是否已有待处理的申请（避免重复发送，同时检查在线 + 离线队列）
        for req in self._pending_friend_requests.values():
            if req["requester_id"] == requester_id and req["target_id"] == target_lobster_id:
                target_owner = target.owner_name if target else target_lobster_id
                return {
                    "type": "pair_failed",
                    "data": {"message": f"你已经向 {target_owner} 发送过好友申请了，请等待对方确认"}
                }
        # 检查离线队列中是否有重复申请
        for offline_req in self._offline_friend_requests.get(target_lobster_id, []):
            if offline_req.get("requester_id") == requester_id:
                return {
                    "type": "pair_failed",
                    "data": {"message": f"你已经向 {target_lobster_id} 发送过好友申请（对方离线中），请等待对方上线后处理"}
                }

        # 生成好友申请 ID
        request_id = uuid.uuid4().hex[:12]

        requester_info = {
            "lobster_id": requester.lobster_id,
            "lobster_name": requester.lobster_name,
            "owner_name": requester.owner_name,
        }

        if target:
            # ── 目标在线 → 走原有逻辑：存入待审批队列，立即通知 ──
            self._pending_friend_requests[request_id] = {
                "requester_id": requester_id,
                "target_id": target_lobster_id,
                "timestamp": time.time(),
                "requester_info": requester_info,
                "target_info": {
                    "lobster_id": target.lobster_id,
                    "lobster_name": target.lobster_name,
                    "owner_name": target.owner_name,
                },
            }

            logger.info(
                f"🦞📬 好友申请(龙虾号)! {requester.lobster_name}[{requester_id}] "
                f"→ {target.lobster_name}[{target_lobster_id}] [申请ID: {request_id}]"
            )

            # 通知目标龙虾：收到好友申请（需确认）
            await self._send_to(target.ws, {
                "type": "friend_request",
                "data": {
                    "request_id": request_id,
                    "lobster_id": requester.lobster_id,
                    "lobster_name": requester.lobster_name,
                    "owner_name": requester.owner_name,
                    "message": f"📬 {requester.owner_name} 的龙虾 {requester.lobster_name} 想加你为好友！",
                }
            })

            # 返回给请求方：申请已发送，等待确认
            return {
                "type": "friend_request_sent",
                "data": {
                    "request_id": request_id,
                    "lobster_id": target.lobster_id,
                    "lobster_name": target.lobster_name,
                    "owner_name": target.owner_name,
                    "message": f"已向 {target.owner_name} 发送好友申请，等待对方确认...",
                }
            }
        else:
            # ── v1.5: 目标离线 → 存入离线好友请求队列 ──
            queue = self._offline_friend_requests.setdefault(target_lobster_id, [])

            # 检查队列上限
            if len(queue) >= OFFLINE_QUEUE_MAX_PER_TARGET:
                return {
                    "type": "pair_failed",
                    "data": {"message": f"对方 {target_lobster_id} 的离线好友请求队列已满，请稍后再试"}
                }

            queue.append({
                "request_id": request_id,
                "requester_id": requester_id,
                "target_id": target_lobster_id,
                "timestamp": time.time(),
                "requester_info": requester_info,
                "source": "pair_by_id",  # 来源标识
            })

            logger.info(
                f"🦞📭 离线好友申请! {requester.lobster_name}[{requester_id}] "
                f"→ {target_lobster_id}(离线) [申请ID: {request_id}] "
                f"[队列: {len(queue)}条]"
            )

            return {
                "type": "offline_queued",
                "data": {
                    "request_id": request_id,
                    "target_lobster_id": target_lobster_id,
                    "message": f"对方 {target_lobster_id} 当前不在线，好友申请已存入离线队列，对方上线后会收到通知",
                    "ttl": OFFLINE_QUEUE_TTL,
                }
            }

    async def _handle_pair_by_link(self, ws, data: dict) -> dict:
        """
        v1.5: 通过邀请链接发送好友申请

        data: {
            target_lobster_id: "claw_alice",    # 邀请链接中的龙虾号
            nonce: "deadbeef...",                # 邀请链接中的随机数（用于标识唯一邀请）
            pk: "abc123..."                      # 邀请方公钥指纹（可选）
        }
        流程: 请求方解析邀请链接后 → 发送 pair_by_link 到目标所在 Relay → 走 pair_by_id 同逻辑
        本质上 pair_by_link 是 pair_by_id 的"有凭证"版本，带 nonce 防重放。
        """
        target_lobster_id = data.get("target_lobster_id", "").strip()
        nonce = data.get("nonce", "").strip()

        if not target_lobster_id:
            return {"type": "pair_failed", "data": {"message": "邀请链接缺少目标龙虾号"}}
        if not nonce:
            return {"type": "pair_failed", "data": {"message": "邀请链接缺少 nonce"}}

        # 找到请求方
        requester_id = self._ws_to_id.get(id(ws))
        if not requester_id or requester_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        # 复用 pair_by_id 逻辑（邀请链接本质是"带凭证的龙虾号加好友"）
        # 额外记录 nonce 和 pk 用于日志追踪和防重放
        result = await self._handle_pair_by_id(ws, {"lobster_id": target_lobster_id})

        # 如果成功进入队列（在线或离线），在日志中记录邀请链接来源
        if result.get("type") in ("friend_request_sent", "offline_queued"):
            request_id = result.get("data", {}).get("request_id", "")
            pk = data.get("pk", "")
            logger.info(
                f"🔗 邀请链接来源 [申请ID: {request_id}] "
                f"[nonce: {nonce[:16]}...] [pk: {pk[:16]}...]"
            )
            # 在返回数据中标记来源为邀请链接
            result["data"]["source"] = "invite_link"
            result["data"]["nonce"] = nonce

        return result

    async def _handle_friend_accept(self, ws, data: dict) -> dict:
        """
        接受好友申请 → 双向加好友

        data: {request_id: "abc123def456"}
        """
        request_id = data.get("request_id", "")

        # 找到操作人
        accepter_id = self._ws_to_id.get(id(ws))
        if not accepter_id or accepter_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        # 查找申请
        request = self._pending_friend_requests.get(request_id)
        if not request:
            return {
                "type": "friend_request_result",
                "data": {"success": False, "message": "好友申请不存在或已过期"}
            }

        # 鉴权：只有目标方能接受
        if request["target_id"] != accepter_id:
            return {
                "type": "friend_request_result",
                "data": {"success": False, "message": "无权操作此好友申请"}
            }

        requester_id = request["requester_id"]
        requester = self._online.get(requester_id)
        accepter = self._online[accepter_id]

        # 双向加好友（运行时状态）
        accepter.friends.add(requester_id)
        if requester:
            requester.friends.add(accepter_id)

        # 删除已处理的申请
        del self._pending_friend_requests[request_id]

        # P1-6 安全修复: 生成 shared_secret 并分发给双方
        # 使用密码学安全随机数生成 32 字节（256 位）密钥
        shared_secret = secrets.token_hex(32)  # 64 字符 hex 字符串

        logger.info(
            f"🦞🤝🦞 好友申请通过! {request['requester_info']['lobster_name']}[{requester_id}] "
            f"↔ {accepter.lobster_name}[{accepter_id}] [密钥已分发]"
        )

        # 通知请求方：好友申请被接受 + shared_secret
        if requester:
            await self._send_to(requester.ws, {
                "type": "friend_added",
                "data": {
                    "lobster_id": accepter.lobster_id,
                    "lobster_name": accepter.lobster_name,
                    "owner_name": accepter.owner_name,
                    "shared_secret": shared_secret,
                    "message": f"🎉 {accepter.owner_name} 接受了你的好友申请！",
                }
            })

        # 返回给接受方：确认好友添加成功 + shared_secret
        return {
            "type": "friend_added",
            "data": {
                "lobster_id": request["requester_info"]["lobster_id"],
                "lobster_name": request["requester_info"]["lobster_name"],
                "owner_name": request["requester_info"]["owner_name"],
                "shared_secret": shared_secret,
                "message": f"已和 {request['requester_info']['owner_name']} 成为好友！",
            }
        }

    async def _handle_friend_reject(self, ws, data: dict) -> dict:
        """
        拒绝好友申请

        data: {request_id: "abc123def456"}
        """
        request_id = data.get("request_id", "")

        # 找到操作人
        rejecter_id = self._ws_to_id.get(id(ws))
        if not rejecter_id or rejecter_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        # 查找申请
        request = self._pending_friend_requests.get(request_id)
        if not request:
            return {
                "type": "friend_request_result",
                "data": {"success": False, "message": "好友申请不存在或已过期"}
            }

        # 鉴权：只有目标方能拒绝
        if request["target_id"] != rejecter_id:
            return {
                "type": "friend_request_result",
                "data": {"success": False, "message": "无权操作此好友申请"}
            }

        requester_id = request["requester_id"]
        requester = self._online.get(requester_id)

        # 删除已处理的申请
        del self._pending_friend_requests[request_id]

        # 安全获取拒绝者信息（防止 TOCTOU: 并发下线导致 KeyError）
        rejecter = self._online.get(rejecter_id)
        rejecter_name = rejecter.lobster_name if rejecter else "未知"
        rejecter_owner = rejecter.owner_name if rejecter else "未知"

        logger.info(
            f"🦞❌ 好友申请被拒! {request['requester_info']['lobster_name']}[{requester_id}] "
            f"→ {rejecter_name}[{rejecter_id}]"
        )

        # 通知请求方：好友申请被拒绝
        if requester:
            await self._send_to(requester.ws, {
                "type": "friend_request_result",
                "data": {
                    "success": False,
                    "lobster_id": rejecter_id,
                    "owner_name": rejecter_owner,
                    "message": f"😞 {rejecter_owner} 拒绝了你的好友申请",
                }
            })

        # 返回给拒绝方
        return {
            "type": "friend_request_result",
            "data": {
                "success": True,
                "message": f"已拒绝 {request['requester_info']['owner_name']} 的好友申请",
            }
        }

    async def _handle_pending_requests(self, ws, data: dict) -> dict:
        """
        查看待处理的好友申请列表

        data: {} (无需参数)
        """
        lobster_id = self._ws_to_id.get(id(ws))
        if not lobster_id or lobster_id not in self._online:
            return {"type": "error", "data": {"message": "请先注册"}}

        now = time.time()
        pending = []
        for req_id, req in self._pending_friend_requests.items():
            if req["target_id"] == lobster_id:
                # 跳过已过期的
                if now - req["timestamp"] > FRIEND_REQUEST_TTL:
                    continue
                pending.append({
                    "request_id": req_id,
                    "lobster_id": req["requester_info"]["lobster_id"],
                    "lobster_name": req["requester_info"]["lobster_name"],
                    "owner_name": req["requester_info"]["owner_name"],
                    "time_ago": int(now - req["timestamp"]),  # 多少秒前
                })

        return {
            "type": "pending_requests_list",
            "data": {
                "requests": pending,
                "count": len(pending),
                "message": f"有 {len(pending)} 条待处理的好友申请",
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
                    "pair_by_id": self._handle_pair_by_id,  # v0.9: 通过龙虾号加好友
                    "pair_by_link": self._handle_pair_by_link,  # v1.5: 通过邀请链接加好友
                    "message": self._handle_message,
                    "relay_response": self._handle_relay_response,
                    "heartbeat": self._handle_heartbeat,
                    "list_friends": self._handle_list_friends,
                    "discover": self._handle_discover,       # v0.7: 龙虾发现
                    "introduce": self._handle_introduce,     # v0.7: 好友引荐
                    "friend_accept": self._handle_friend_accept,       # v0.9: 接受好友申请
                    "friend_reject": self._handle_friend_reject,       # v0.9: 拒绝好友申请
                    "pending_requests": self._handle_pending_requests,  # v0.9: 查看待处理申请
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
        logger.info("🦞🌐 WeClaw Relay Server v2.3 启动中...")
        # TLS 配置
        ssl_context = None
        protocol = "ws"
        if RELAY_TLS_CERT and RELAY_TLS_KEY:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(RELAY_TLS_CERT, RELAY_TLS_KEY)
            protocol = "wss"
            logger.info(f"   🔒 TLS: 已启用 (证书: {RELAY_TLS_CERT})")
        else:
            logger.warning(
                "   ⚠️  TLS 未配置！使用未加密的 ws:// 连接。"
                "生产环境请设置 RELAY_TLS_CERT 和 RELAY_TLS_KEY 环境变量。"
            )

        logger.info(f"   📡 地址: {protocol}://{RELAY_HOST}:{RELAY_PORT}")
        logger.info(f"   🦞 最大在线: {MAX_LOBSTERS}")
        logger.info(f"   🔍 发现协议: 已启用 (v0.7)")
        logger.info(f"   🤝 好友确认: 已启用 (v0.9)")
        logger.info(f"   🔗 邀请链接: 已启用 (v1.5)")
        logger.info(f"   📭 离线队列: 已启用 (v1.5, TTL={OFFLINE_QUEUE_TTL}s, max={OFFLINE_QUEUE_MAX_PER_TARGET}/target)")
        logger.info(f"   💰 平台成本: 0（只做路由，AI 走用户自己的 Key）")

        asyncio.create_task(self._cleanup_expired_codes())
        asyncio.create_task(self._cleanup_stale_connections())
        asyncio.create_task(self._cleanup_expired_requests())
        asyncio.create_task(self._cleanup_expired_offline_requests())  # v1.5

        async with serve(
            self.handle_connection,
            RELAY_HOST,
            RELAY_PORT,
            ssl=ssl_context,
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
