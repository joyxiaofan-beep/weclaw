"""
终端模式引擎 — 独立运行核心

将 ContactMemory + StateStore + TerminalChannel + C2C 组装在一起，
提供完整的龙虾交互体验。

🦞↔🦞 终端模式也支持龙虾互联！
- 通过 Relay Server 自动连接（零配置，无需公网 IP）
- 邀请码配对：一条命令连接另一只龙虾
- 龙虾握手/传话/通讯录/信任 全套 C2C 指令
- 也支持 HTTP 直连模式（高级用户）
- 通讯录持久化到 SQLite，重启不丢失

v1.1.0: AI Brain 已移除，WeClaw 是纯通信协议 SDK。
终端模式保留为演示 / 调试工具，不再依赖 AI。

用法：
    python -m weclaw              # 默认开启 Relay 模式
    python -m weclaw --no-relay   # 不连 Relay（离线模式）
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from weclaw.brain.core import MessageIntent, ReplyDigest, mask_api_key
from weclaw.channel.terminal import TerminalChannel, _print_lobster, _print_system, _C
from weclaw.memory.contacts import ContactMemory
from weclaw.memory.store import StateStore

# C2C 龙虾互联
from weclaw.claw2claw.protocol import (
    AgentCard,
    AgentBehavior,  # v0.8
    AgentCapability,
    C2CMessage,
    PeerInfo,
    PersistentPeerRegistry,
    validate_lobster_id,      # v0.9
    generate_lobster_id,      # v0.9
    LOBSTER_ID_PREFIX,        # v0.9
)
from weclaw.claw2claw.client import C2CClient
from weclaw.claw2claw.handler import C2CHandler
from weclaw.claw2claw.relay import RelayClient


# ──────────────────────────────────────────
# 配置加载
# ──────────────────────────────────────────

def _load_terminal_config() -> dict:
    """
    加载终端模式配置

    优先级：
    1. config/config.yaml（如果存在）
    2. 环境变量（Relay 相关）
    """
    config = {
        "behavior": {
            "confirm_before_send": True,
            "tone": "auto",
            "context_window_size": 15,
        },
        "storage": {
            "db_path": "data/weclaw_state.db",
        },
        "claw2claw": {
            "enabled": True,  # 默认启用（Relay 模式零配置）
            "lobster_id": "",
            "lobster_name": "🦞 我的龙虾",
            "owner_name": "我",
            "my_endpoint": "",
            "c2c_port": 8766,  # 终端模式 C2C 监听端口（HTTP 直连）
            "relay_url": "ws://localhost:8900",  # Relay Server 地址
            "relay_enabled": True,  # 是否使用 Relay
            "capabilities": [],
            "known_peers": [],
        },
    }

    # 尝试从已有配置文件加载
    config_path = Path("config/config.yaml")
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                full_config = yaml.safe_load(f) or {}
            if "behavior" in full_config:
                config["behavior"].update(full_config["behavior"])
            if "storage" in full_config:
                config["storage"].update(full_config["storage"])
            if "claw2claw" in full_config:
                config["claw2claw"].update(full_config["claw2claw"])
            _print_system("已加载 config/config.yaml")
        except Exception as e:
            _print_system(f"配置文件加载失败: {e}")

    # Relay 环境变量
    env_relay = os.environ.get("RELAY_URL", "")
    if env_relay:
        config["claw2claw"]["relay_url"] = env_relay

    return config


# ──────────────────────────────────────────
# 终端引擎
# ──────────────────────────────────────────

class TerminalEngine:
    """
    终端模式引擎

    组装 Memory + Store + Channel + C2C，
    提供完整的龙虾交互循环，包括 Relay 龙虾互联。

    v1.1.0: 不再依赖 Brain（AI 大脑），纯通信协议。
    """

    def __init__(self, config: dict):
        self.config = config

        # 初始化核心组件
        self.contact_memory = ContactMemory(data_dir="data/contacts")

        db_path = config.get("storage", {}).get("db_path", "data/weclaw_state.db")
        self.state_store = StateStore(db_path=db_path)

        self.channel = TerminalChannel(owner_name="你")

        # 待确认消息缓存
        self._pending_drafts: dict[str, dict] = {}

        # ── 🦞↔🦞 C2C 龙虾互联 ──
        c2c_config = config.get("claw2claw", {})
        self.c2c_enabled = c2c_config.get("enabled", True)
        self.c2c_client: Optional[C2CClient] = None
        self.c2c_handler: Optional[C2CHandler] = None
        self.peer_registry: Optional[PersistentPeerRegistry] = None
        self.my_card: Optional[AgentCard] = None
        self._c2c_server = None  # asyncio HTTP server
        self._relay_client: Optional[RelayClient] = None
        self._relay_enabled = c2c_config.get("relay_enabled", True)

        if self.c2c_enabled:
            self._init_c2c(c2c_config)

    def _init_c2c(self, c2c_config: dict):
        """初始化 C2C 龙虾互联组件（含 Relay）"""
        import uuid

        # 构建龙虾名片（v2: 持久龙虾号 — 首次生成后存入 SQLite，重启不变）
        # v0.9: 统一 claw_ 前缀，支持用户自定义
        lobster_id = c2c_config.get("lobster_id") or ""
        if not lobster_id:
            # 从数据库恢复
            lobster_id = self.state_store.get_setting("lobster_id") or ""
        if not lobster_id:
            # 首次启动：让用户自定义龙虾号
            lobster_id = self._prompt_lobster_id()
            self.state_store.set_setting("lobster_id", lobster_id)
            _print_system(f"🆕 龙虾号已设定: {_C.BOLD}{_C.CYAN}{lobster_id}{_C.RESET}")
        else:
            # 确保也存入 DB（配置文件指定的 lobster_id 也要持久化）
            existing = self.state_store.get_setting("lobster_id")
            if existing != lobster_id:
                self.state_store.set_setting("lobster_id", lobster_id)
        self.my_card = AgentCard(
            lobster_id=lobster_id,
            lobster_name=c2c_config.get("lobster_name", "🦞 我的龙虾"),
            owner_name=c2c_config.get("owner_name", "我"),
            endpoint=c2c_config.get("my_endpoint", ""),
            capabilities=[
                AgentCapability(name=c.get("name", ""), description=c.get("description", ""))
                for c in c2c_config.get("capabilities", [])
            ] or [
                AgentCapability(name="relay_message", description="代主人传话"),
                AgentCapability(name="check_availability", description="查看主人是否方便"),
            ],
        )

        # v0.8: 从配置加载 Agent Profile（结构化画像）
        profile_config = c2c_config.get("agent_profile", {})
        if profile_config:
            self.my_card.description = profile_config.get("description", "")
            self.my_card.services_offered = profile_config.get("services_offered", [])
            self.my_card.services_needed = profile_config.get("services_needed", [])
            self.my_card.interests = profile_config.get("interests", [])
            self.my_card.welcome_bubbles = profile_config.get("welcome_bubbles", [])
            self.my_card.values = profile_config.get("values", [])
            self.my_card.personal_looking_for = profile_config.get("personal_looking_for", "")
            self.my_card.industries = profile_config.get("industries", [])
            self.my_card.location_area = profile_config.get("location_area", "")
            self.my_card.language = profile_config.get("language", "zh")
            self.my_card.page_public = profile_config.get("page_public", True)
            # 同时持久化到 SQLite
            self.state_store.save_agent_profile_dict(profile_config)

        # v0.8: 从配置加载 Agent Behavior（行为规则）
        behavior_config = c2c_config.get("agent_behavior", {})
        self._agent_behavior = AgentBehavior(**behavior_config) if behavior_config else AgentBehavior()

        # 如果使用 Relay 模式，endpoint 设为 relay://lobster_id
        if self._relay_enabled and not self.my_card.endpoint:
            self.my_card.endpoint = f"relay://{lobster_id}"

        # 持久化通讯录
        self.peer_registry = PersistentPeerRegistry(self.state_store)

        # 加载配置中的 known_peers
        for peer_data in c2c_config.get("known_peers", []):
            if peer_data.get("endpoint"):
                peer = PeerInfo(
                    lobster_id=peer_data.get("lobster_id", f"peer-{uuid.uuid4().hex[:6]}"),
                    lobster_name=peer_data.get("lobster_name", ""),
                    owner_name=peer_data.get("owner_name", ""),
                    endpoint=peer_data["endpoint"],
                    shared_secret=peer_data.get("shared_secret", ""),
                    trusted=peer_data.get("trusted", False),
                )
                self.peer_registry.add_peer(peer)

        # C2C 消息处理器（notify_owner 通过终端打印）
        self.c2c_handler = C2CHandler(
            my_card=self.my_card,
            peer_registry=self.peer_registry,
            notify_owner_fn=lambda msg: _print_lobster(msg),
            state_store=self.state_store,
            behavior=self._agent_behavior,  # v0.8
        )

        # Relay Client（如果启用）— v2 协议回调 + v0.7 discover/introduce 回调
        if self._relay_enabled:
            relay_url = c2c_config.get("relay_url", "ws://localhost:8900")
            self._relay_client = RelayClient(
                my_card=self.my_card,
                peer_registry=self.peer_registry,
                relay_url=relay_url,
                on_message=self._handle_relay_incoming,
                on_friend_added=self._handle_relay_friend_added,
                on_friend_request=self._handle_relay_friend_request,  # v0.9: 好友申请
                on_friend_online=self._handle_relay_friend_online,
                on_friend_offline=self._handle_relay_friend_offline,
                on_discover_result=self._handle_relay_discover_result,    # v0.7
                on_introduction=self._handle_relay_introduction,          # v0.7
            )

        # C2C 客户端（注入 relay_client）
        self.c2c_client = C2CClient(self.my_card, self.peer_registry, relay_client=self._relay_client)

        peer_count = len(self.peer_registry.list_peers())
        mode = "Relay 模式" if self._relay_enabled else "HTTP 直连模式"
        _print_system(
            f"🦞↔🦞 龙虾互联已启用（{mode}）— {self.my_card.lobster_name} "
            f"(通讯录: {peer_count} 只龙虾)"
        )

    # ──────────────────────────────────────────
    # 🦞 v0.9: 龙虾号自定义
    # ──────────────────────────────────────────

    def _prompt_lobster_id(self) -> str:
        """
        首次启动时让用户自定义龙虾号。

        规则：
        - 前缀 claw_ 自动添加，用户只输入自定义部分
        - 3-20 个字符，小写字母+数字+下划线，字母开头
        - 直接回车则自动生成随机龙虾号
        """
        _print_lobster(
            f"🆕 首次启动！请设定你的龙虾号：\n\n"
            f"  格式: {_C.BOLD}{LOBSTER_ID_PREFIX}<你的自定义名>{_C.RESET}\n"
            f"  规则: 3-20 个字符，小写字母+数字+下划线，字母开头\n"
            f"  示例: {LOBSTER_ID_PREFIX}alice, {LOBSTER_ID_PREFIX}bob_2024, {LOBSTER_ID_PREFIX}dev_team\n"
            f"\n"
            f"  直接按回车 → 自动生成随机龙虾号"
        )

        while True:
            try:
                user_input = input(f"\n  {_C.BOLD}请输入自定义部分（{LOBSTER_ID_PREFIX}）: {_C.RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                # 非交互环境或用户中断 → 自动生成
                auto_id = generate_lobster_id()
                _print_system(f"自动生成龙虾号: {auto_id}")
                return auto_id

            if not user_input:
                # 直接回车 → 自动生成
                auto_id = generate_lobster_id()
                _print_system(f"✨ 自动生成龙虾号: {_C.BOLD}{_C.CYAN}{auto_id}{_C.RESET}")
                return auto_id

            # 用户可能输入了完整的 claw_xxx 或只输入了 xxx
            if user_input.startswith(LOBSTER_ID_PREFIX):
                lobster_id = user_input
            else:
                lobster_id = f"{LOBSTER_ID_PREFIX}{user_input}"

            # 验证格式
            valid, error = validate_lobster_id(lobster_id)
            if valid:
                return lobster_id
            else:
                _print_system(f"❌ {error}，请重试。")

    # ──────────────────────────────────────────
    # 🌐 Relay 回调 & 连接管理
    # ──────────────────────────────────────────

    async def _handle_relay_incoming(self, incoming: C2CMessage) -> Optional[C2CMessage]:
        """
        处理通过 Relay 收到的消息

        与 HTTP 模式走相同的 C2CHandler 逻辑。
        """
        reply = self.c2c_handler.handle(incoming)

        # 记录到对话上下文
        if incoming.msg_type in ("message", "query"):
            self.state_store.add_conversation(
                role="contact",
                content=f"[🦞C2C] {incoming.from_owner_name}: {incoming.content}",
                speaker=f"{incoming.from_owner_name}的龙虾",
                metadata={"type": "c2c_incoming", "msg_type": incoming.msg_type},
            )

        return reply

    async def _handle_relay_friend_added(self, data: dict):
        """Relay 加好友成功回调（v2）— 通知用户"""
        friend_name = data.get("lobster_name", "未知龙虾")
        owner_name = data.get("owner_name", "未知")
        _print_lobster(
            f"🦞🤝🦞 有龙虾通过加好友码添加了你！\n\n"
            f"🦞 {friend_name}\n"
            f"👤 主人: {owner_name}\n\n"
            f"你们已经是好友了！\n"
            f"现在可以用 \"龙虾传话 {owner_name} <消息>\" 来传话！"
        )

    async def _handle_relay_friend_request(self, data: dict):
        """v0.9: 收到好友申请通知 — 提示用户同意/拒绝"""
        owner_name = data.get("owner_name", "未知")
        lobster_name = data.get("lobster_name", "")
        request_id = data.get("request_id", "")
        _print_lobster(
            f"📬 收到好友申请!\n\n"
            f"🦞 {lobster_name}\n"
            f"👤 主人: {owner_name}\n\n"
            f"输入 \"{_C.BOLD}龙虾同意 {owner_name}{_C.RESET}\" 接受\n"
            f"输入 \"{_C.BOLD}龙虾拒绝 {owner_name}{_C.RESET}\" 拒绝\n"
            f"输入 \"{_C.BOLD}龙虾申请列表{_C.RESET}\" 查看所有待处理申请"
        )

    async def _handle_relay_friend_online(self, data: dict):
        """Relay 好友上线通知（v2 新增）"""
        friend_name = data.get("lobster_name", "未知龙虾")
        owner_name = data.get("owner_name", "未知")
        _print_system(f"🦞✅ 好友 {friend_name} ({owner_name}) 上线了")

    async def _handle_relay_friend_offline(self, data: dict):
        """Relay 好友下线通知（v2，替代旧的 peer_disconnected）"""
        friend_name = data.get("lobster_name", "未知龙虾")
        owner_name = data.get("owner_name", "未知")
        _print_system(f"🦞❌ 好友 {friend_name} ({owner_name}) 已离线")

    async def _handle_relay_discover_result(self, data: dict):
        """v0.7: 龙虾发现结果回调"""
        matches = data.get("matches", [])
        total = data.get("total_online", 0)

        if not matches:
            await self.channel.send_to_owner(
                f"🔍 在线龙虾共 {total} 只，但没有找到匹配的。\n"
                f"💡 试试不带标签搜索: \"龙虾发现\""
            )
            return

        lines = [f"🔍 发现 {len(matches)} 只龙虾（共 {total} 只在线）：\n"]
        for m in matches:
            handle_str = f" ({m.get('handle', '')})" if m.get("handle") else ""
            tags_str = f"  🏷️ {', '.join(m.get('tags', []))}" if m.get("tags") else ""
            lines.append(
                f"  🦞 {m.get('lobster_name', '?')}{handle_str}\n"
                f"     👤 主人: {m.get('owner_name', '?')}\n"
                f"     🆔 {m.get('lobster_id', '?')}"
                f"{tags_str}"
            )
        lines.append(f"\n💡 可以让你的好友引荐，或通过加好友码添加。")
        await self.channel.send_to_owner("\n".join(lines))

    async def _handle_relay_introduction(self, data: dict):
        """v0.7: 好友引荐通知回调"""
        introducer = data.get("from_owner_name", "未知")
        introduced = data.get("introduced_peer", {})
        reason = data.get("reason", "")

        lines = [
            f"🦞🤝 收到引荐！\n",
            f"  📬 {introducer} 把 {introduced.get('owner_name', '未知')} 介绍给你",
        ]
        if reason:
            lines.append(f"  💬 理由: {reason}")
        lines.append(f"\n  🦞 {introduced.get('lobster_name', '?')}")
        lines.append(f"  🆔 {introduced.get('lobster_id', '?')}")
        if introduced.get("tags"):
            lines.append(f"  🏷️ {', '.join(introduced['tags'])}")
        lines.append(f"\n  对方已加入你的通讯录（信任分: 5/100）")
        lines.append(f"  回复 \"龙虾信任 {introduced.get('owner_name', '')}\" 来信任对方。")

        await self.channel.send_to_owner("\n".join(lines))

    async def _proactive_discovery_loop(self):
        """
        v0.8: 主动发现循环

        按设定间隔自动搜索匹配龙虾，推荐给主人。
        """
        interval = self._agent_behavior.proactive.discovery_interval_minutes * 60
        await asyncio.sleep(30)  # 启动后延迟 30s 再开始

        while True:
            try:
                if self._relay_client and self._relay_client.connected:
                    # 构建搜索标签：基于自己的 interests + tags
                    search_tags = list(set(
                        (self.my_card.interests or []) + (self.my_card.tags or [])
                    ))[:5]  # 最多 5 个标签

                    if search_tags:
                        _print_system(f"🔍 主动发现中（标签: {', '.join(search_tags)}）...")
                        await self._relay_client._send({
                            "type": "discover",
                            "data": {
                                "tags": search_tags,
                                "limit": self._agent_behavior.proactive.max_recommendations,
                            }
                        })
                    else:
                        logger.debug("主动发现: 没有 interests/tags，跳过本轮")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"主动发现异常: {e}")

            await asyncio.sleep(interval)

    async def _start_relay(self) -> bool:
        """
        连接到 Relay Server

        Returns:
            是否成功连接
        """
        if not self._relay_client:
            return False

        _print_system("🌐 正在连接 Relay Server...")

        connected = await self._relay_client.connect()
        if connected:
            pair_code = self._relay_client.pair_code
            _print_system(
                f"✅ Relay 已连接！\n"
                f"\n"
                f"   🦞 你的龙虾号: {_C.BOLD}{_C.CYAN}{self.my_card.lobster_id}{_C.RESET}\n"
                f"   📋 加好友码: {_C.BOLD}{_C.CYAN}{pair_code}{_C.RESET}（面对面快捷方式，一次性）\n"
                f"\n"
                f"   朋友可以通过龙虾号加你:\n"
                f"   {_C.BOLD}龙虾加好友 {self.my_card.lobster_id}{_C.RESET}\n"
                f"\n"
                f"   面对面加好友（把加好友码发给对方）:\n"
                f"   {_C.BOLD}龙虾加好友 {pair_code}{_C.RESET}"
            )
            return True
        else:
            _print_system(
                "⚠️ Relay 连接失败（Relay Server 可能未启动）\n"
                "   龙虾互联的 Relay 模式不可用。\n"
                "   你仍可使用 HTTP 直连模式：\"龙虾握手 http://对方地址\"\n"
                "   或启动 Relay: python relay_server/server.py"
            )
            return False

    async def _start_c2c_server(self):
        """启动内嵌的 C2C HTTP 服务器（接收远程龙虾消息，HTTP 直连模式）"""
        from aiohttp import web

        c2c_config = self.config.get("claw2claw", {})
        port = c2c_config.get("c2c_port", 8766)

        app = web.Application()
        app.router.add_post("/c2c/incoming", self._handle_c2c_http)
        app.router.add_get("/c2c/card", self._handle_c2c_card)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        try:
            await site.start()
            self._c2c_server = runner
            _print_system(f"🦞↔🦞 C2C 监听端口 {port} — 远程龙虾可以通过 /c2c/incoming 联系我")

            # 如果 endpoint 未设置，提示用户
            if not self.my_card.endpoint:
                _print_system(
                    f"💡 其他龙虾需要你的地址才能联系你。\n"
                    f"     本地测试: http://localhost:{port}\n"
                    f"     公网: 用 ngrok 等工具做端口映射"
                )
        except OSError as e:
            _print_system(f"⚠️ C2C 服务器启动失败（端口 {port} 被占用？）: {e}")
            _print_system("   龙虾互联的「接收」功能不可用，但你仍可主动发起握手和传话")

    async def _handle_c2c_http(self, request):
        """处理远程龙虾发来的 HTTP 消息"""
        from aiohttp import web
        from weclaw.claw2claw.protocol import C2CMessage as C2CMsg

        try:
            data = await request.json()
            incoming = C2CMsg(**data)
            reply = self.c2c_handler.handle(incoming)

            # 记录到对话上下文
            if incoming.msg_type in ("message", "query"):
                self.state_store.add_conversation(
                    role="contact",
                    content=f"[🦞C2C] {incoming.from_owner_name}: {incoming.content}",
                    speaker=f"{incoming.from_owner_name}的龙虾",
                    metadata={"type": "c2c_incoming", "msg_type": incoming.msg_type},
                )

            return web.json_response(reply.model_dump())
        except Exception as e:
            logger.error(f"C2C HTTP 处理异常: {e}")
            return web.json_response({"error": str(e)}, status=400)

    async def _handle_c2c_card(self, request):
        """返回公开龙虾名片"""
        from aiohttp import web
        return web.json_response(self.my_card.to_public())

    async def run(self):
        """主循环"""
        await self.channel.start()

        # 启动 Relay 连接（如果启用）
        if self.c2c_enabled and self._relay_enabled and self._relay_client:
            await self._start_relay()

        # 启动 C2C HTTP 服务器（如果配置了 endpoint 且非纯 Relay 模式）
        if self.c2c_enabled and self.c2c_handler:
            c2c_config = self.config.get("claw2claw", {})
            my_endpoint = c2c_config.get("my_endpoint", "")
            # 只有配置了 HTTP endpoint 才启动 HTTP 服务器
            if my_endpoint and my_endpoint.startswith("http"):
                await self._start_c2c_server()

        self._print_welcome()

        # v0.8: 启动主动发现循环（如果启用）
        discovery_task = None
        if (self.c2c_enabled and self._relay_enabled
                and hasattr(self, '_agent_behavior')
                and self._agent_behavior.proactive.enabled):
            discovery_task = asyncio.create_task(self._proactive_discovery_loop())
            _print_system(
                f"🔍 主动发现已启用（每 {self._agent_behavior.proactive.discovery_interval_minutes} 分钟）"
            )

        while True:
            msg = await self.channel.receive()
            if msg is None:
                break

            try:
                if msg.is_from_owner:
                    await self._handle_owner_command(msg.content)
                else:
                    await self._handle_incoming_message(
                        msg.sender_name, msg.content
                    )
            except Exception as e:
                logger.error(f"处理消息异常: {e}", exc_info=True)
                await self.channel.send_to_owner(f"⚠️ 出了点问题：{e}")

        # 清理
        if discovery_task:
            discovery_task.cancel()
        if self.c2c_client:
            await self.c2c_client.close()
        if self._relay_client:
            await self._relay_client.disconnect()
        if self._c2c_server:
            await self._c2c_server.cleanup()
        self.state_store.close()
        _print_system("龙虾已退出，数据已保存。")

    def _print_welcome(self):
        """欢迎信息"""
        contact_count = len(self.contact_memory.list_contacts())
        stats = self.state_store.stats()

        lines = ["🦞 龙虾已就位！"]

        status_parts = []
        if contact_count > 0:
            status_parts.append(f"记得 {contact_count} 个人")
        pending = stats.get("pending_messages", 0)
        if pending > 0:
            status_parts.append(f"{pending} 条消息待处理")

        # C2C 龙虾通讯录状态
        if self.c2c_enabled and self.peer_registry:
            peer_count = len(self.peer_registry.list_peers())
            trusted_count = len(self.peer_registry.list_trusted())
            if peer_count > 0:
                status_parts.append(f"认识 {peer_count} 只龙虾（{trusted_count} 只已信任）")

        if status_parts:
            lines.append(f"📊 {'，'.join(status_parts)}")

        lines.append("")
        lines.append("试试跟我说：")
        lines.append("• \"帮我问 Alice 今天有空吗\" — 生成代发消息")
        lines.append("• \"谁懂数据分析\" — 从记忆中找人")
        lines.append("• \"待办\" — 查看待跟进事项")

        if self.c2c_enabled:
            lines.append("")
            lines.append("🦞↔🦞 龙虾互联指令：")
            if self._relay_enabled and self._relay_client and self._relay_client.connected:
                lines.append(f"• 你的龙虾号: {_C.BOLD}{self.my_card.lobster_id}{_C.RESET}")
                lines.append(f"• 加好友码: {_C.BOLD}{self._relay_client.pair_code}{_C.RESET}（面对面快捷方式）")
                lines.append(f"• \"龙虾加好友 claw_alice\" — 通过龙虾号发送好友申请")
                lines.append("• \"龙虾加好友 #1234\" — 通过加好友码发送好友申请（面对面）")
            lines.append("• \"龙虾同意 Alice\" — 接受好友申请")
            lines.append("• \"龙虾拒绝 Alice\" — 拒绝好友申请")
            lines.append("• \"龙虾申请列表\" — 查看待处理的好友申请")
            lines.append("• \"龙虾传话 Alice 明天开会\" — 给好友龙虾传话")
            lines.append("• \"龙虾通讯录\" — 查看好友列表")
            lines.append("• \"龙虾回 Alice 好的收到\" — 回复龙虾消息")
            lines.append("• \"龙虾发现 数据分析\" — 按标签搜索在线龙虾")
            lines.append("• \"龙虾引荐 Alice Bob\" — 把好友介绍给另一个好友")
            # v0.8 新指令
            lines.append("")
            lines.append("🦞 v0.8 龙虾画像 & 对话：")
            lines.append("• \"龙虾画像\" — 查看我的龙虾画像")
            lines.append("• \"龙虾设置\" — 查看行为规则设置")
            lines.append("• \"龙虾对话\" — 查看所有对话线程")
            lines.append("• \"龙虾对话 Alice\" — 查看与 Alice 的聊天记录")
            if not self._relay_enabled:
                lines.append("• \"龙虾握手 http://xxx\" — 和远程龙虾交换名片（HTTP 直连）")

        if contact_count == 0 and not self.c2c_enabled:
            lines.append("")
            lines.append("📋 通讯录为空，先添加联系人吧：")
            lines.append("• \"小王是做数据分析的，部门是技术部\"")
            lines.append("• 或 @小王 你好啊 — 模拟收到小王的消息")

        _print_lobster("\n".join(lines))

    # ──────────────────────────────────────────
    # 主人指令处理
    # ──────────────────────────────────────────

    async def _handle_owner_command(self, command: str):
        """处理主人指令"""

        # 记录到对话上下文
        self.state_store.add_conversation(
            role="owner", content=command, metadata={"type": "command"}
        )

        # ── 快速路径：确认/取消/修改 ──
        if re.match(r"^发送\s*\d+", command):
            mid = re.search(r"\d+", command).group()
            await self._confirm_pending(mid, approved=True)
            return

        if re.match(r"^取消\s*\d+", command):
            mid = re.search(r"\d+", command).group()
            await self._confirm_pending(mid, approved=False)
            return

        if re.match(r"^改\s*\d+\s+.+", command):
            match = re.match(r"^改\s*(\d+)\s+(.+)", command)
            mid, new_content = match.group(1), match.group(2)
            await self._confirm_pending(mid, approved=True, edited_content=new_content)
            return

        # ── 🦞↔🦞 C2C 快速路径 ──
        if self.c2c_enabled:
            # v0.9: 龙虾加好友 claw_xxx（通过龙虾号）或 #1234（通过加好友码）
            m = re.match(r"^龙虾加好友\s+(claw_\S+)", command)
            if m:
                await self._handle_relay_add_friend_by_id(m.group(1).strip())
                return

            m = re.match(r"^龙虾加好友\s+#?(\d+)", command)
            if m:
                await self._handle_relay_add_friend(f"#{m.group(1)}")
                return

            # v0.9: 龙虾同意 <名字>（接受好友申请）
            m = re.match(r"^龙虾同意\s+(\S+)", command)
            if m:
                await self._handle_accept_friend(m.group(1))
                return

            # v0.9: 龙虾拒绝 <名字>（拒绝好友申请）
            m = re.match(r"^龙虾拒绝\s+(\S+)", command)
            if m:
                await self._handle_reject_friend(m.group(1))
                return

            # v0.9: 龙虾申请列表（查看待处理的好友申请）
            if re.match(r"^(龙虾申请列表|龙虾申请|好友申请)", command):
                await self._handle_list_friend_requests()
                return

            # 兼容旧命令：龙虾连接 #1234 或 龙虾连接 CLAW-XXXX
            m = re.match(r"^龙虾连接\s+(#?\S+)", command, re.IGNORECASE)
            if m:
                code = m.group(1).strip()
                await self._handle_relay_add_friend(code)
                return

            # 龙虾号 / 加好友码 / 我的龙虾号
            if re.match(r"^(龙虾号|加好友码|我的龙虾号|我的加好友码|龙虾邀请码|我的邀请码|邀请码)", command):
                await self._handle_show_pair_code()
                return

            # 龙虾传话 <名字> <内容>
            m = re.match(r"^龙虾传话\s+(\S+)\s+(.+)", command, re.DOTALL)
            if m:
                await self._handle_c2c_relay(m.group(1), m.group(2))
                return

            # 龙虾回 <名字> <内容>
            m = re.match(r"^龙虾回\s+(\S+)\s+(.+)", command, re.DOTALL)
            if m:
                await self._handle_c2c_reply(m.group(1), m.group(2))
                return

            # 龙虾握手 <URL> [密钥]（HTTP 直连）
            m = re.match(r"^龙虾握手\s+((?:https?|relay)://\S+)(?:\s+(\S+))?", command)
            if m:
                await self._handle_c2c_handshake(m.group(1), m.group(2) or "")
                return

            # 龙虾通讯录 / 龙虾列表 / 其他龙虾
            if re.match(r"^(龙虾通讯录|龙虾列表|其他龙虾)", command):
                await self._handle_c2c_list_peers()
                return

            # 龙虾信任 <名字>
            m = re.match(r"^龙虾信任\s+(\S+)", command)
            if m:
                await self._handle_c2c_trust(m.group(1))
                return

            # 龙虾删除 <名字>
            m = re.match(r"^龙虾删除\s+(\S+)", command)
            if m:
                await self._handle_c2c_remove(m.group(1))
                return

            # v0.7: 龙虾发现 [标签1 标签2 ...]
            m = re.match(r"^龙虾发现(?:\s+(.+))?", command)
            if m:
                tags_str = m.group(1) or ""
                tags = [t.strip() for t in tags_str.split() if t.strip()]
                await self._handle_c2c_discover(tags)
                return

            # v0.7: 龙虾引荐 <A> <B> [理由]
            m = re.match(r"^龙虾引荐\s+(\S+)\s+(\S+)(?:\s+(.+))?", command)
            if m:
                await self._handle_c2c_introduce(m.group(1), m.group(2), m.group(3) or "")
                return

            # v0.8: 龙虾画像 — 查看/编辑我的画像
            if re.match(r"^(龙虾画像|龙虾简介|我的画像|龙虾资料)", command):
                await self._handle_c2c_profile()
                return

            # v0.8: 龙虾设置 — 查看/编辑行为规则
            if re.match(r"^(龙虾设置|龙虾行为|行为规则)", command):
                await self._handle_c2c_settings()
                return

            # v0.8: 龙虾对话 [名字] — 查看对话线程
            m = re.match(r"^龙虾对话(?:\s+(\S+))?", command)
            if m:
                await self._handle_c2c_threads(m.group(1))
                return

        # ── 简单命令匹配（v1.1.0: 不再使用 AI 意图解析）──

        # 找人
        m = re.match(r"^(?:谁懂|谁知道|谁了解|谁擅长|找个?人?.{0,2}懂)\s*(.+)", command)
        if m:
            intent = MessageIntent(action="find_person", topic=m.group(1).strip(), raw_instruction=command)
            await self._handle_find_person(intent)
            return

        # 查看回复
        if re.match(r"^(?:.*回复了吗|查看.*消息|未读|收件箱)", command):
            m2 = re.match(r"^(\S+?)回复了吗", command)
            target = m2.group(1) if m2 else None
            intent = MessageIntent(action="check_reply", target_name=target, raw_instruction=command)
            await self._handle_check_reply(intent)
            return

        # 待办 / 待跟进
        if re.match(r"^(?:待办|待跟进|提醒|跟进)", command):
            await self._handle_check_reminders()
            return

        # 发消息（帮我问 / 帮我告诉 / 给XX发 / 跟XX说）
        m = re.match(r"^(?:帮我(?:问|告诉|跟)|给(\S+?)(?:发|说)|跟(\S+?)说)\s*(.+)", command)
        if m:
            target = m.group(1) or m.group(2) or ""
            gist = m.group(3) or ""
            if target:
                await self.channel.send_to_owner(
                    f"📝 v1.1.0 终端模式不再内置 AI 消息生成。\n\n"
                    f"你可以直接用 C2C 传话：\n"
                    f"  龙虾传话 {target} {gist}\n\n"
                    f"或使用 SDK 接入你自己的 AI 来生成消息。"
                )
            else:
                await self.channel.send_to_owner(
                    "🤔 请指定发给谁，比如：\"帮我问小王今天有空吗\""
                )
            return

        # 通用未识别 — 给出帮助提示
        c2c_hints = ""
        if self.c2c_enabled:
            c2c_hints = (
                "\n• \"龙虾传话 Alice …\" — 给 Alice 的龙虾传话\n"
                "• \"龙虾通讯录\" — 查看认识的龙虾"
            )
        await self.channel.send_to_owner(
            "🦞 收到，但我不太确定你想做什么。\n\n"
            "你可以试试：\n"
            "• \"谁懂XX\" — 找人\n"
            "• \"XX回复了吗\" — 查看回复\n"
            "• \"待办\" — 查看待跟进事项"
            + c2c_hints
        )

    async def _handle_find_person(self, intent: MessageIntent):
        """处理找人指令"""
        results = self.contact_memory.find_by_topic(
            intent.topic or intent.raw_instruction
        )
        if results:
            lines = ["🔍 找到以下相关的人：\n"]
            for profile, score in results[:5]:
                expertise_str = ", ".join(profile.expertise[:3]) if profile.expertise else "未知"
                lines.append(f"• {profile.name} — {expertise_str} (相关度: {score})")
            await self.channel.send_to_owner("\n".join(lines))
        else:
            await self.channel.send_to_owner(
                f"🤔 暂时没有找到跟「{intent.topic or intent.raw_instruction}」相关的人。\n\n"
                f"💡 你可以告诉我谁擅长这个领域，比如：\"小王很懂数据分析\""
            )

    async def _handle_check_reply(self, intent: MessageIntent):
        """处理查看回复"""
        all_pending = self.state_store.list_pending()
        incoming = {
            k: v for k, v in all_pending.items()
            if v.get("from") and (
                not intent.target_name or intent.target_name in v.get("from", "")
            )
        }
        if incoming:
            lines = [f"📬 你有 {len(incoming)} 条未读消息：\n"]
            for mid, msg in incoming.items():
                digest = msg.get("digest", {})
                key_info = digest.get("key_info", msg.get("content", "")[:50])
                lines.append(f"• {msg['from']}: {key_info}")
                if digest.get("action_needed"):
                    lines.append(f"  ⚡ 需要你跟进")
            await self.channel.send_to_owner("\n".join(lines))
        else:
            await self.channel.send_to_owner("📭 暂时没有未处理的消息。")

    async def _handle_check_reminders(self):
        """处理查看待办"""
        active = self.state_store.list_active_trackers()
        if active:
            lines = [f"⏰ 你有 {len(active)} 条待跟进：\n"]
            for tid, info in active.items():
                reminded = " (已提醒)" if info["reminded"] else ""
                lines.append(
                    f"• {info['target']} — "
                    f"{info.get('topic', '未知话题')}{reminded}\n"
                    f"  发送于 {info['sent_at'][:16]}"
                )
            await self.channel.send_to_owner("\n".join(lines))
        else:
            await self.channel.send_to_owner("✅ 暂时没有待跟进的事项。")

    async def _confirm_pending(
        self, mid: str, approved: bool, edited_content: str = None
    ):
        """处理确认/取消/修改"""
        msg = self.state_store.get_pending(mid)
        if not msg:
            await self.channel.send_to_owner(f"⚠️ 消息 #{mid} 不存在或已处理。")
            return

        if not approved:
            self.state_store.delete_pending(mid)
            await self.channel.send_to_owner(f"🚫 消息 #{mid} 已取消。")
            return

        content = edited_content or msg["content"]
        target_name = msg.get("target_name", "未知")

        # 终端模式：展示"发送"效果
        await self.channel.send(target_name, content)

        self.contact_memory.record_interaction(
            name=target_name,
            direction="outgoing",
            summary=content[:100],
        )
        self.state_store.delete_pending(mid)

        await self.channel.send_to_owner(f"✅ 已\"发送\"给 {target_name}")
        self.state_store.add_conversation(
            role="lobster",
            content=f"已发送消息给 {target_name}",
            metadata={"type": "sent", "target": target_name},
        )

        # 追踪
        tid = str(self.state_store.next_id("tracker"))
        from datetime import datetime
        self.state_store.save_tracker(
            tid, target_name, datetime.now().isoformat()
        )

    # ──────────────────────────────────────────
    # 🦞🌐 Relay 命令处理
    # ──────────────────────────────────────────

    async def _handle_relay_add_friend(self, pair_code: str):
        """龙虾加好友 #1234 — 通过加好友码添加好友（v2）"""
        if not self._relay_client or not self._relay_client.connected:
            await self.channel.send_to_owner(
                "⚠️ Relay 未连接，无法加好友。\n"
                "请先确保 Relay Server 已启动: python relay_server/server.py"
            )
            return

        await self.channel.send_to_owner(f"🦞🔗 正在通过加好友码 {pair_code} 添加好友...")

        result = await self._relay_client.add_friend_by_code(pair_code)

        if result:
            owner_name = result.get("owner_name", "未知")

            await self.channel.send_to_owner(
                f"📬 好友申请已发送！\n\n"
                f"等待 {owner_name} 确认...\n"
                f"对方同意后你们就是好友了。\n\n"
                f"💡 输入 \"{_C.BOLD}龙虾申请列表{_C.RESET}\" 查看申请状态"
            )
        else:
            await self.channel.send_to_owner(
                f"🦞🔗❌ 加好友失败\n\n"
                f"加好友码 {pair_code} 可能已过期或无效。\n"
                f"请确认对方龙虾在线，且加好友码正确。\n"
                f"💡 加好友码是一次性的，每次上线会生成新的。"
            )

    async def _handle_relay_add_friend_by_id(self, lobster_id: str):
        """v0.9: 龙虾加好友 claw_xxx — 通过龙虾号发送好友申请"""
        if not self._relay_client or not self._relay_client.connected:
            await self.channel.send_to_owner(
                "⚠️ Relay 未连接，无法加好友。\n"
                "请先确保 Relay Server 已启动: python relay_server/server.py"
            )
            return

        await self.channel.send_to_owner(f"🦞🔗 正在通过龙虾号 {lobster_id} 发送好友申请...")

        result = await self._relay_client.add_friend_by_id(lobster_id)

        if result:
            owner_name = result.get("owner_name", "未知")

            await self.channel.send_to_owner(
                f"📬 好友申请已发送！\n\n"
                f"等待 {owner_name} 确认...\n"
                f"对方同意后你们就是好友了。\n\n"
                f"💡 输入 \"{_C.BOLD}龙虾申请列表{_C.RESET}\" 查看申请状态"
            )
        else:
            await self.channel.send_to_owner(
                f"🦞🔗❌ 加好友失败\n\n"
                f"龙虾号 {lobster_id} 对应的龙虾可能不在线或不存在。\n"
                f"请确认龙虾号拼写正确，且对方龙虾在线。"
            )

    async def _handle_accept_friend(self, name: str):
        """v0.9: 龙虾同意 <名字> — 接受好友申请"""
        if not self._relay_client or not self._relay_client.connected:
            await self.channel.send_to_owner("⚠️ Relay 未连接，无法处理好友申请。")
            return

        # 从 pending 列表中按 owner_name 查找 request_id
        pending = self._relay_client._pending_friend_requests
        request_id = None
        for rid, req in pending.items():
            if req.get("owner_name", "").lower() == name.lower():
                request_id = rid
                break

        if not request_id:
            await self.channel.send_to_owner(
                f"🦞 找不到来自 {name} 的好友申请。\n\n"
                f"输入 \"{_C.BOLD}龙虾申请列表{_C.RESET}\" 查看所有待处理申请。"
            )
            return

        await self.channel.send_to_owner(f"🦞🤝 正在接受 {name} 的好友申请...")
        result = await self._relay_client.accept_friend(request_id)

        if result:
            await self.channel.send_to_owner(
                f"🦞🤝✅ 已接受 {name} 的好友申请！\n\n"
                f"你们已经是好友了！\n"
                f"现在可以用 \"{_C.BOLD}龙虾传话 {name} <消息>{_C.RESET}\" 来传话！"
            )
        else:
            await self.channel.send_to_owner(
                f"⚠️ 接受好友申请失败，申请可能已过期。\n"
                f"请对方重新发送好友申请。"
            )

    async def _handle_reject_friend(self, name: str):
        """v0.9: 龙虾拒绝 <名字> — 拒绝好友申请"""
        if not self._relay_client or not self._relay_client.connected:
            await self.channel.send_to_owner("⚠️ Relay 未连接，无法处理好友申请。")
            return

        # 从 pending 列表中按 owner_name 查找 request_id
        pending = self._relay_client._pending_friend_requests
        request_id = None
        for rid, req in pending.items():
            if req.get("owner_name", "").lower() == name.lower():
                request_id = rid
                break

        if not request_id:
            await self.channel.send_to_owner(
                f"🦞 找不到来自 {name} 的好友申请。\n\n"
                f"输入 \"{_C.BOLD}龙虾申请列表{_C.RESET}\" 查看所有待处理申请。"
            )
            return

        await self.channel.send_to_owner(f"🦞 正在拒绝 {name} 的好友申请...")
        result = await self._relay_client.reject_friend(request_id)

        if result:
            await self.channel.send_to_owner(
                f"🦞❌ 已拒绝 {name} 的好友申请。"
            )
        else:
            await self.channel.send_to_owner(
                f"⚠️ 拒绝好友申请失败，申请可能已过期。"
            )

    async def _handle_list_friend_requests(self):
        """v0.9: 龙虾申请列表 — 查看待处理的好友申请"""
        if not self._relay_client or not self._relay_client.connected:
            await self.channel.send_to_owner("⚠️ Relay 未连接，无法查看好友申请。")
            return

        # 先从本地缓存展示
        pending = self._relay_client._pending_friend_requests

        if not pending:
            await self.channel.send_to_owner(
                "📭 暂无待处理的好友申请。\n\n"
                "💡 把你的加好友码发给朋友，让他们来加你！"
            )
            # 同时向服务器请求最新列表（异步刷新）
            await self._relay_client.list_pending_requests()
            return

        lines = [f"📬 待处理的好友申请（{len(pending)} 条）：\n"]
        for rid, req in pending.items():
            owner_name = req.get("owner_name", "未知")
            lobster_name = req.get("lobster_name", "")
            lines.append(
                f"  🦞 {lobster_name}\n"
                f"     👤 主人: {owner_name}\n"
                f"     → 输入 \"{_C.BOLD}龙虾同意 {owner_name}{_C.RESET}\" 接受\n"
                f"     → 输入 \"{_C.BOLD}龙虾拒绝 {owner_name}{_C.RESET}\" 拒绝"
            )
        await self.channel.send_to_owner("\n".join(lines))

        # 同时向服务器请求最新列表（异步刷新）
        await self._relay_client.list_pending_requests()

    async def _handle_show_pair_code(self):
        """显示龙虾号和当前加好友码"""
        lines = [f"🦞 你的龙虾号: {_C.BOLD}{self.my_card.lobster_id}{_C.RESET}"]

        if self._relay_client and self._relay_client.connected:
            pair_code = self._relay_client.pair_code
            lines.append(f"📋 加好友码: {_C.BOLD}{pair_code}{_C.RESET}（一次性，加完即弃）")
            lines.append("")
            lines.append("把加好友码发给朋友，对方输入:")
            lines.append(f"  龙虾加好友 {pair_code}")
            lines.append("即可成为好友！")
            lines.append("")
            lines.append("💡 加好友码仅用于首次添加，加完后按龙虾号直连。")
        else:
            lines.append("⚠️ Relay 未连接，没有加好友码。")
            lines.append("请确保 Relay Server 已启动。")

        await self.channel.send_to_owner("\n".join(lines))

    # ──────────────────────────────────────────
    # 🦞↔🦞 C2C 命令处理
    # ──────────────────────────────────────────

    async def _handle_c2c_relay(self, target_name: str, content: str):
        """龙虾传话 — 通过 C2C 给对方龙虾传话"""
        peer = self.peer_registry.find_by_name(target_name)
        if not peer:
            await self.channel.send_to_owner(
                f"🦞 找不到叫 {target_name} 的龙虾。\n\n"
                f"请先用 \"龙虾握手 <对方URL>\" 交换名片。\n"
                f"或输入 \"龙虾通讯录\" 查看已知龙虾。"
            )
            return

        if not peer.trusted:
            await self.channel.send_to_owner(
                f"⚠️ {peer.lobster_name} 还未信任，不能传话。\n"
                f"请先用 \"龙虾信任 {target_name}\" 信任对方。"
            )
            return

        await self.channel.send_to_owner(f"🦞→🦞 正在给 {peer.lobster_name} 传话...")

        reply = await self.c2c_client.send_message(
            peer, content, msg_type="message"
        )

        if reply:
            await self.channel.send_to_owner(
                f"🦞→🦞 传话成功!\n\n"
                f"📤 发给 {peer.owner_name} 的龙虾: 「{content}」\n"
                f"📥 对方龙虾回复: 「{reply.content}」"
            )
            self.state_store.add_conversation(
                role="lobster",
                content=f"🦞→🦞 给 {peer.owner_name} 的龙虾传话: {content[:50]}… 对方回复: {reply.content[:50]}",
                metadata={"type": "c2c_relay", "target_owner": peer.owner_name},
            )
        else:
            await self.channel.send_to_owner(
                f"🦞✖🦞 传话失败，可能对方龙虾不在线。\n"
                f"📡 对方地址: {peer.endpoint}"
            )

    async def _handle_c2c_reply(self, target_name: str, content: str):
        """龙虾回 — 回复某只龙虾的消息（自动关联 reply_to）"""
        peer = self.peer_registry.find_by_name(target_name)
        if not peer:
            await self.channel.send_to_owner(
                f"🦞 找不到叫 {target_name} 的龙虾。输入 \"龙虾通讯录\" 查看已知龙虾。"
            )
            return

        if not peer.trusted:
            await self.channel.send_to_owner(
                f"⚠️ {peer.lobster_name} 还未信任，请先 \"龙虾信任 {target_name}\"。"
            )
            return

        # 从 inbox 查找最近一条来自该发送方的消息作为 reply_to
        reply_to_id = None
        if self.c2c_handler:
            inbox = self.c2c_handler.get_inbox(limit=50)
            for msg_item in reversed(inbox):
                if (target_name.lower() in msg_item.get("from_owner", "").lower()
                        or target_name.lower() in msg_item.get("from_lobster", "").lower()):
                    reply_to_id = msg_item.get("message_id")
                    break

        await self.channel.send_to_owner(f"🦞→🦞 正在回复 {peer.lobster_name}...")

        reply = await self.c2c_client.send_message(
            peer, content, msg_type="message", reply_to=reply_to_id
        )

        if reply:
            await self.channel.send_to_owner(
                f"🦞→🦞 回复成功!\n\n"
                f"📤 回复 {peer.owner_name}: 「{content}」\n"
                f"📥 对方龙虾: 「{reply.content}」"
            )
            self.state_store.add_conversation(
                role="lobster",
                content=f"🦞→🦞 回复 {peer.owner_name}: {content[:50]}…",
                metadata={"type": "c2c_reply", "target_owner": peer.owner_name},
            )
        else:
            await self.channel.send_to_owner(
                f"🦞✖🦞 回复失败，对方龙虾可能不在线。"
            )

    async def _handle_c2c_handshake(self, endpoint: str, secret: str):
        """龙虾握手 — 向远程龙虾发起握手"""
        await self.channel.send_to_owner(f"🦞🤝 正在向 {endpoint} 发起握手...")

        peer_info = await self.c2c_client.handshake(endpoint, shared_secret=secret)

        if peer_info:
            await self.channel.send_to_owner(
                f"🦞🤝✅ 握手成功!\n\n"
                f"🦞 龙虾名: {peer_info.lobster_name}\n"
                f"👤 主人: {peer_info.owner_name}\n"
                f"🎯 能力: {', '.join(peer_info.capabilities) or '未知'}\n\n"
                f"现在你可以用 \"龙虾传话 {peer_info.owner_name} <消息>\" 来传话了！"
            )
        else:
            await self.channel.send_to_owner(
                f"🦞🤝❌ 握手失败\n\n"
                f"📡 地址: {endpoint}\n"
                f"请检查对方龙虾是否在线，地址是否正确。"
            )

    async def _handle_c2c_list_peers(self):
        """龙虾通讯录 — 显示好友列表"""
        peers = self.peer_registry.list_peers()

        if not peers:
            await self.channel.send_to_owner(
                "🦞 还没有好友。\n\n"
                "使用 \"龙虾加好友 #XXXX\" 来交个朋友！"
            )
            return

        lines = [f"🦞 好友列表 ({len(peers)} 只)：\n"]
        for p in peers:
            trust_icon = "✅" if p.trusted else "⏳"
            trust_bar = "█" * (p.trust_score // 10) + "░" * (10 - p.trust_score // 10)
            tags_str = f"  🏷️ {', '.join(p.tags)}" if p.tags else ""
            handle_str = f" ({p.handle})" if p.handle else ""
            desc_str = f"\n     📝 {p.description}" if p.description else ""
            introduced_str = ""
            if hasattr(p, 'introduced_by') and p.introduced_by:
                introducer = self.peer_registry.get_peer(p.introduced_by)
                if introducer:
                    introduced_str = f"\n     🤝 由 {introducer.owner_name} 引荐"
            lines.append(
                f"  {trust_icon} {p.lobster_name}{handle_str} (主人: {p.owner_name})\n"
                f"     🆔 龙虾号: {p.lobster_id}\n"
                f"     💯 信任: [{trust_bar}] {p.trust_score}/100\n"
                f"     最后通信: {p.last_seen or '从未'}"
                f"{desc_str}{tags_str}{introduced_str}"
            )

        # 汇总提示
        trusted_count = sum(1 for p in peers if p.trusted)
        untrusted = [p for p in peers if not p.trusted]
        if untrusted:
            lines.append(f"\n⏳ 有 {len(untrusted)} 只龙虾等待信任（信任分 < 70）")
            lines.append(f"   回复 \"龙虾信任 <名字>\" 来信任。")

        lines.append(f"\n📊 共 {len(peers)} 只好友，{trusted_count} 只已信任")
        await self.channel.send_to_owner("\n".join(lines))

    async def _handle_c2c_trust(self, name: str):
        """龙虾信任 — 信任指定龙虾（v0.7: 提升信任分到 70+）"""
        peer = self.peer_registry.find_by_name(name)

        if not peer:
            await self.channel.send_to_owner(
                f"🦞 找不到叫 {name} 的龙虾。输入 \"龙虾通讯录\" 查看已知龙虾。"
            )
            return

        if peer.trusted:
            await self.channel.send_to_owner(
                f"✅ {peer.lobster_name} 已经是信任状态了（信任分: {peer.trust_score}/100）。"
            )
            return

        # v0.7: 手动信任 → trust_score 提升到 70
        old_score = peer.trust_score
        peer.trust_score = max(peer.trust_score, 70)
        self.peer_registry.add_peer(peer)  # 更新持久化

        # 记录信任事件
        if self.state_store:
            delta = peer.trust_score - old_score
            self.state_store.log_trust_event(
                peer.lobster_id, "manual_trust", delta,
                f"主人手动信任 {peer.owner_name}"
            )

        await self.channel.send_to_owner(
            f"✅ 已信任 {peer.lobster_name} (主人: {peer.owner_name})\n"
            f"💯 信任分: {old_score} → {peer.trust_score}/100\n\n"
            f"现在你可以用 \"龙虾传话 {peer.owner_name} <消息>\" 来传话了！"
        )

    async def _handle_c2c_remove(self, name: str):
        """龙虾删除 — 从通讯录移除指定龙虾"""
        peer = self.peer_registry.find_by_name(name)

        if not peer:
            await self.channel.send_to_owner(
                f"🦞 找不到叫 {name} 的龙虾。输入 \"龙虾通讯录\" 查看已知龙虾。"
            )
            return

        lobster_name = peer.lobster_name
        owner_name = peer.owner_name
        self.peer_registry.remove_peer(peer.lobster_id)

        await self.channel.send_to_owner(
            f"🗑️ 已从通讯录移除 {lobster_name} (主人: {owner_name})\n\n"
            f"如需重新连接，使用 \"龙虾握手 <对方URL>\"。"
        )

    async def _handle_c2c_discover(self, tags: list[str]):
        """v0.7: 龙虾发现 — 通过 Relay 搜索在线龙虾"""
        if not self._relay_client or not self._relay_client.connected:
            await self.channel.send_to_owner(
                "🦞 未连接到 Relay Server，无法使用发现功能。\n"
                "请先确保 Relay 连接正常。"
            )
            return

        _print_system(f"正在搜索{'标签: ' + ', '.join(tags) if tags else '所有在线'}龙虾...")

        try:
            # 通过 Relay 发送 discover 请求
            await self._relay_client._send({
                "type": "discover",
                "data": {"tags": tags, "limit": 10}
            })
            await self.channel.send_to_owner(
                f"🔍 已发送发现请求{'（标签: ' + ', '.join(tags) + '）' if tags else ''}...\n"
                f"结果将在收到后显示。"
            )
        except Exception as e:
            await self.channel.send_to_owner(f"🦞 发现请求失败: {e}")

    async def _handle_c2c_introduce(self, name_a: str, name_b: str, reason: str = ""):
        """v0.7: 龙虾引荐 — 把 A 介绍给 B"""
        if not self._relay_client or not self._relay_client.connected:
            await self.channel.send_to_owner(
                "🦞 未连接到 Relay Server，无法使用引荐功能。"
            )
            return

        peer_a = self.peer_registry.find_by_name(name_a)
        peer_b = self.peer_registry.find_by_name(name_b)

        if not peer_a:
            await self.channel.send_to_owner(f"🦞 找不到叫 {name_a} 的龙虾。")
            return
        if not peer_b:
            await self.channel.send_to_owner(f"🦞 找不到叫 {name_b} 的龙虾。")
            return

        if not peer_a.trusted or not peer_b.trusted:
            await self.channel.send_to_owner(
                "🦞 引荐需要双方都是你的已信任好友。\n"
                f"  {peer_a.lobster_name}: {'✅ 已信任' if peer_a.trusted else '⏳ 未信任'}\n"
                f"  {peer_b.lobster_name}: {'✅ 已信任' if peer_b.trusted else '⏳ 未信任'}"
            )
            return

        try:
            await self._relay_client._send({
                "type": "introduce",
                "data": {
                    "target_id": peer_a.lobster_id,
                    "introduce_to_id": peer_b.lobster_id,
                    "reason": reason,
                }
            })
            await self.channel.send_to_owner(
                f"🦞🤝🦞 引荐已发送！\n\n"
                f"把 {peer_a.owner_name} 介绍给 {peer_b.owner_name}\n"
                f"{'💬 理由: ' + reason if reason else ''}\n\n"
                f"双方会收到通知。"
            )
        except Exception as e:
            await self.channel.send_to_owner(f"🦞 引荐发送失败: {e}")

    # ──────────────────────────────────────────
    # 🦞 v0.8: 龙虾画像 / 设置 / 对话线程
    # ──────────────────────────────────────────

    async def _handle_c2c_profile(self):
        """龙虾画像 — 查看我的龙虾画像"""
        card = self.my_card
        services_offered = ", ".join(card.services_offered) if card.services_offered else "未设置"
        services_needed = ", ".join(card.services_needed) if card.services_needed else "未设置"
        interests_str = ", ".join(card.interests) if card.interests else "未设置"
        values_str = ", ".join(card.values) if card.values else "未设置"
        industries_str = ", ".join(card.industries) if card.industries else "未设置"
        tags_str = ", ".join(card.tags) if card.tags else "未设置"
        welcome_str = "\n".join(f"    💭 {b}" for b in card.welcome_bubbles) if card.welcome_bubbles else "    未设置"

        lines = [
            f"🦞 我的龙虾画像\n",
            f"  🆔 龙虾号: {card.lobster_id}",
            f"  🦞 名字: {card.lobster_name}",
            f"  👤 主人: {card.owner_name}",
            f"  🏷️ 别名: {card.handle or '未设置'}",
            f"",
            f"  📝 简介: {card.description or '未设置'}",
            f"  🎯 我能提供: {services_offered}",
            f"  🔍 我在找: {services_needed}",
            f"  💡 兴趣: {interests_str}",
            f"  🏢 行业: {industries_str}",
            f"  📍 地区: {card.location_area or '未设置'}",
            f"  🌐 语言: {card.language}",
            f"  💎 价值观: {values_str}",
            f"  🏷️ 标签: {tags_str}",
            f"  🤝 我想认识: {card.personal_looking_for or '未设置'}",
            f"  📖 公开主页: {'是' if card.page_public else '否'}",
            f"",
            f"  💬 Welcome Bubbles（首次见面问候）：",
            welcome_str,
            f"",
            f"💡 编辑画像请修改 config/config.yaml 中的 claw2claw.agent_profile 部分",
        ]
        await self.channel.send_to_owner("\n".join(lines))

    async def _handle_c2c_settings(self):
        """龙虾设置 — 查看当前行为规则"""
        b = self._agent_behavior

        # 安全规则
        sec = b.security
        protected_str = ", ".join(sec.protected_fields) if sec.protected_fields else "无"
        block_kw_str = ", ".join(sec.block_keywords) if sec.block_keywords else "无"

        # 主动发现
        pro = b.proactive

        # 过滤规则
        flt = b.filter
        block_list_str = ", ".join(flt.block_list) if flt.block_list else "无"

        # 定时任务
        tasks_lines = []
        for t in b.scheduled_tasks:
            status = "✅" if t.enabled else "❌"
            tasks_lines.append(f"    {status} {t.name}: {t.action} ({t.cron})")
        tasks_str = "\n".join(tasks_lines) if tasks_lines else "    无"

        lines = [
            f"🦞 龙虾行为设置\n",
            f"  🔒 安全规则：",
            f"    保护字段: {protected_str}",
            f"    拦截关键词: {block_kw_str}",
            f"    消息速率限制: {sec.max_messages_per_minute} 条/分钟",
            f"",
            f"  🔍 主动发现：",
            f"    启用: {'✅ 是' if pro.enabled else '❌ 否'}",
            f"    发现间隔: {pro.discovery_interval_minutes} 分钟",
            f"    自动握手: {'是' if pro.auto_handshake else '否'}",
            f"    匹配阈值: {pro.match_threshold}",
            f"    最大推荐数: {pro.max_recommendations}",
            f"",
            f"  🚫 过滤规则：",
            f"    最低信任分: {flt.min_trust_to_message}",
            f"    黑名单: {block_list_str}",
            f"    需先握手: {'是' if flt.require_handshake else '否'}",
            f"",
            f"  ⏰ 定时任务：",
            tasks_str,
            f"",
            f"  📝 额外系统提示: {b.system_prompt_extra[:50] + '...' if b.system_prompt_extra else '无'}",
            f"",
            f"💡 编辑设置请修改 config/config.yaml 中的 claw2claw.agent_behavior 部分",
        ]
        await self.channel.send_to_owner("\n".join(lines))

    async def _handle_c2c_threads(self, name: Optional[str] = None):
        """龙虾对话 — 查看对话线程"""
        if not self.state_store:
            await self.channel.send_to_owner("⚠️ 存储未初始化，无法查看对话。")
            return

        if name:
            # 查看与特定龙虾的对话记录
            peer = self.peer_registry.find_by_name(name)
            if not peer:
                await self.channel.send_to_owner(
                    f"🦞 找不到叫 {name} 的龙虾。输入 \"龙虾通讯录\" 查看已知龙虾。"
                )
                return

            messages = self.state_store.get_thread_messages(peer.lobster_name, limit=20)
            if not messages:
                await self.channel.send_to_owner(
                    f"📭 与 {peer.owner_name} 的龙虾暂无对话记录。"
                )
                return

            # 标记为已读
            marked = self.state_store.mark_thread_read(peer.lobster_name)

            lines = [f"🦞💬 与 {peer.owner_name}（{peer.lobster_name}）的对话：\n"]
            for msg in messages:
                ts = msg["received_at"][11:16] if msg.get("received_at") else "??:??"
                read_icon = "" if msg.get("is_read") else " 🔴"
                lines.append(f"  [{ts}] {msg['from_owner']}: {msg['content']}{read_icon}")

            if marked > 0:
                lines.append(f"\n✅ 已将 {marked} 条消息标记为已读")
            await self.channel.send_to_owner("\n".join(lines))
        else:
            # 列出所有对话线程
            threads = self.state_store.list_c2c_threads()
            if not threads:
                await self.channel.send_to_owner("📭 暂无龙虾对话记录。")
                return

            unread_total = self.state_store.c2c_unread_count()
            lines = [f"🦞💬 对话列表（共 {len(threads)} 个对话，{unread_total} 条未读）：\n"]

            for t in threads:
                unread_badge = f" 🔴{t['unread_count']}" if t['unread_count'] > 0 else ""
                ts = t["last_time"][5:16] if t.get("last_time") else "未知"
                preview = t["last_message"][:30] + ("..." if len(t["last_message"]) > 30 else "")
                lines.append(
                    f"  💬 {t['from_owner']}（{t['from_lobster']}）{unread_badge}\n"
                    f"     [{ts}] {preview}\n"
                    f"     共 {t['total_count']} 条消息"
                )

            lines.append(f"\n💡 输入 \"龙虾对话 <名字>\" 查看与某人的聊天详情")
            await self.channel.send_to_owner("\n".join(lines))

    # ──────────────────────────────────────────
    # 处理"收到的"消息（模拟联系人回复）
    # ──────────────────────────────────────────

    async def _handle_incoming_message(self, from_name: str, content: str):
        """处理联系人回复（终端模式下通过 @name 模拟）"""

        # 记录到对话上下文
        self.state_store.add_conversation(
            role="contact",
            content=content,
            speaker=from_name,
        )

        # 记录交互（不再使用 AI 摘要）
        self.contact_memory.record_interaction(
            name=from_name,
            direction="incoming",
            summary=content[:100],
            raw_content=content,
        )

        # 清除超时追踪
        resolved = self.state_store.resolve_tracker(from_name)

        # 存入收件箱
        mid = str(self.state_store.next_id("pending"))
        self.state_store.save_pending(mid, {
            "type": "incoming",
            "from": from_name,
            "content": content,
            "digest": {"key_info": content[:200], "action_needed": False},
        })

        # 组装通知
        lines = [f"📨 收到 {from_name} 的消息："]
        lines.append(f"  💬 \"{content}\"")

        if resolved:
            lines.append(f"\n  ✅ 已清除 {len(resolved)} 条等待 {from_name} 回复的追踪")

        await self.channel.send_to_owner("\n".join(lines))

    def _record_sent(self, intent: MessageIntent, content: str):
        """记录发送"""
        if intent.target_name:
            self.contact_memory.record_interaction(
                name=intent.target_name,
                direction="outgoing",
                summary=intent.message_gist or content[:50],
                topics=[intent.topic] if intent.topic else [],
            )
            self.state_store.add_conversation(
                role="lobster",
                content=f"已发送消息给 {intent.target_name}",
                metadata={"type": "sent", "target": intent.target_name},
            )


# ──────────────────────────────────────────
# 入口
# ──────────────────────────────────────────

def main(no_relay: bool = False):
    """终端模式启动入口"""
    # 配置日志（终端模式降低日志噪音）
    logger.remove()
    logger.add(
        "data/logs/terminal_{time:YYYY-MM-DD}.log",
        rotation="10 MB",
        level="DEBUG",
        format="{time:HH:mm:ss} | {level:<7} | {message}",
    )
    # 终端只显示 WARNING 以上
    logger.add(
        sys.stderr,
        level="WARNING",
        format="{message}",
    )

    # 加载配置
    config = _load_terminal_config()

    # 如果指定了 --no-relay，禁用 Relay
    if no_relay:
        config["claw2claw"]["relay_enabled"] = False

    # 启动引擎
    engine = TerminalEngine(config)
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        _print_lobster("被打断了…拜拜 💤")


if __name__ == "__main__":
    main()
