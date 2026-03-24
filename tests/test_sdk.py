"""
WeClaw SDK 测试 — 验证 SDK 公共 API

测试覆盖：
1. 导入与基础创建
2. 身份持久化（lobster_id 重启不变）
3. 通讯录操作
4. 回调注册（装饰器 & 直接调用）
5. 上下文管理器
6. 错误处理（未启动时调用）
"""

import asyncio
import pytest

from weclaw import WeClaw, __version__
from weclaw.claw2claw.protocol import AgentCard, C2CMessage, PeerInfo


# ──────────────────────────────────────────
# 导入测试
# ──────────────────────────────────────────

class TestSDKImport:
    """SDK 可以正常导入"""

    def test_import_weclaw_class(self):
        from weclaw import WeClaw
        assert WeClaw is not None

    def test_import_from_sdk_module(self):
        from weclaw.sdk import WeClaw
        assert WeClaw is not None

    def test_version_updated(self):
        assert __version__ == "1.2.0"


# ──────────────────────────────────────────
# 创建测试
# ──────────────────────────────────────────

class TestSDKCreate:
    """SDK 实例化（不需要 start）"""

    def test_create_default(self):
        claw = WeClaw()
        assert repr(claw) == "<WeClaw name='🦞 未命名龙虾' owner='匿名主人' id=N/A status=stopped>"

    def test_create_with_name(self):
        claw = WeClaw(name="小龙", owner="Alice")
        assert "小龙" in repr(claw)
        assert "Alice" in repr(claw)
        assert "stopped" in repr(claw)

    def test_create_with_full_config(self):
        claw = WeClaw(
            name="测试龙虾",
            owner="测试者",
            handle="@test",
            description="一只测试龙虾",
            tags=["test", "dev"],
            capabilities=["relay_message"],
            services_offered=["测试服务"],
            interests=["AI", "社交"],
            welcome_bubbles=["你好！我是测试龙虾 🦞"],
        )
        assert claw is not None

    def test_not_started_by_default(self):
        claw = WeClaw()
        assert claw.lobster_id == ""
        assert claw.pair_code == ""
        assert claw.connected is False


# ──────────────────────────────────────────
# 启动测试
# ──────────────────────────────────────────

class TestSDKStart:
    """SDK 启动（不连 Relay）"""

    @pytest.mark.asyncio
    async def test_start_without_relay(self, tmp_path):
        claw = WeClaw(
            name="测试龙虾",
            owner="测试者",
            data_dir=str(tmp_path),
        )
        await claw.start(connect_relay=False)

        assert claw.lobster_id.startswith("claw_")
        assert claw.my_card().lobster_name == "测试龙虾"
        assert claw.my_card().owner_name == "测试者"
        assert "started" in repr(claw)

        await claw.stop()

    @pytest.mark.asyncio
    async def test_start_double_raises(self, tmp_path):
        claw = WeClaw(data_dir=str(tmp_path))
        await claw.start(connect_relay=False)

        with pytest.raises(RuntimeError, match="已经启动"):
            await claw.start(connect_relay=False)

        await claw.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self, tmp_path):
        claw = WeClaw(data_dir=str(tmp_path))
        await claw.start(connect_relay=False)
        await claw.stop()
        await claw.stop()  # 第二次不报错


# ──────────────────────────────────────────
# 身份持久化测试
# ──────────────────────────────────────────

class TestIdentityPersistence:
    """lobster_id 在 start/stop/start 后保持不变"""

    @pytest.mark.asyncio
    async def test_lobster_id_persists(self, tmp_path):
        # 第一次启动 → 创建 lobster_id
        claw1 = WeClaw(name="龙虾A", owner="Alice", data_dir=str(tmp_path))
        await claw1.start(connect_relay=False)
        first_id = claw1.lobster_id
        assert first_id.startswith("claw_")
        await claw1.stop()

        # 第二次启动 → 同目录 → 应该加载相同 lobster_id
        claw2 = WeClaw(name="龙虾A改", owner="Alice", data_dir=str(tmp_path))
        await claw2.start(connect_relay=False)
        second_id = claw2.lobster_id
        await claw2.stop()

        assert first_id == second_id


# ──────────────────────────────────────────
# 通讯录测试
# ──────────────────────────────────────────

class TestContacts:
    """通讯录操作"""

    @pytest.mark.asyncio
    async def test_contacts_empty_initially(self, tmp_path):
        claw = WeClaw(data_dir=str(tmp_path))
        await claw.start(connect_relay=False)

        assert claw.contacts() == []

        await claw.stop()

    @pytest.mark.asyncio
    async def test_find_contact_not_found(self, tmp_path):
        claw = WeClaw(data_dir=str(tmp_path))
        await claw.start(connect_relay=False)

        assert claw.find_contact("不存在的龙虾") is None

        await claw.stop()


# ──────────────────────────────────────────
# 回调注册测试
# ──────────────────────────────────────────

class TestCallbacks:
    """回调注册"""

    def test_on_message_decorator(self):
        claw = WeClaw()

        @claw.on_message
        async def handler(sender, content, message):
            pass

        assert claw._on_message_callback is handler

    def test_on_message_direct(self):
        claw = WeClaw()

        async def handler(sender, content, message):
            pass

        claw.on_message(handler)
        assert claw._on_message_callback is handler

    def test_on_friend_request_decorator(self):
        claw = WeClaw()

        @claw.on_friend_request
        async def handler(peer_info, card):
            pass

        assert claw._on_friend_request_callback is handler

    def test_on_friend_online_decorator(self):
        claw = WeClaw()

        @claw.on_friend_online
        async def handler(info):
            pass

        assert claw._on_friend_online_callback is handler

    def test_on_friend_offline_decorator(self):
        claw = WeClaw()

        @claw.on_friend_offline
        async def handler(info):
            pass

        assert claw._on_friend_offline_callback is handler


# ──────────────────────────────────────────
# 上下文管理器测试
# ──────────────────────────────────────────

class TestContextManager:
    """async with 用法"""

    @pytest.mark.asyncio
    async def test_async_with(self, tmp_path):
        async with WeClaw(data_dir=str(tmp_path), connect_relay=False) as claw:
            # start 已在 __aenter__ 中调用（connect_relay=False 跳过 Relay）
            assert claw._started
        # __aexit__ 调用了 stop


# ──────────────────────────────────────────
# 错误处理测试
# ──────────────────────────────────────────

class TestErrorHandling:
    """未启动时调用 API 应该报错"""

    def test_contacts_before_start(self):
        claw = WeClaw()
        with pytest.raises(RuntimeError, match="尚未启动"):
            claw.contacts()

    def test_my_card_before_start(self):
        claw = WeClaw()
        with pytest.raises(RuntimeError, match="尚未启动"):
            claw.my_card()

    def test_find_contact_before_start(self):
        claw = WeClaw()
        with pytest.raises(RuntimeError, match="尚未启动"):
            claw.find_contact("someone")

    @pytest.mark.asyncio
    async def test_send_before_start(self):
        claw = WeClaw()
        with pytest.raises(RuntimeError, match="尚未启动"):
            await claw.send("someone", "hello")

    @pytest.mark.asyncio
    async def test_add_friend_before_start(self):
        claw = WeClaw()
        with pytest.raises(RuntimeError, match="尚未启动"):
            await claw.add_friend("#1234")


# ──────────────────────────────────────────
# 名片测试
# ──────────────────────────────────────────

class TestMyCard:
    """名片内容正确"""

    @pytest.mark.asyncio
    async def test_card_fields(self, tmp_path):
        claw = WeClaw(
            name="小龙虾",
            owner="Bob",
            handle="@bob",
            description="Bob 的小龙虾",
            tags=["AI", "产品"],
            data_dir=str(tmp_path),
        )
        await claw.start(connect_relay=False)

        card = claw.my_card()
        assert card.lobster_name == "小龙虾"
        assert card.owner_name == "Bob"
        assert card.handle == "@bob"
        assert card.description == "Bob 的小龙虾"
        assert "AI" in card.tags
        assert card.endpoint.startswith("relay://")

        await claw.stop()
