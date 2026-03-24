"""
基础测试 — 确保核心模块可以正常导入和初始化
"""

import pytest


class TestImports:
    """测试所有核心模块是否可以正常导入"""

    def test_import_version(self):
        from weclaw import __version__
        assert __version__ == "1.0.0"

    def test_import_brain(self):
        from weclaw.brain.core import Brain, MessageIntent, GeneratedMessage, ReplyDigest
        assert Brain is not None
        assert MessageIntent is not None

    def test_import_contacts(self):
        from weclaw.memory.contacts import ContactMemory
        assert ContactMemory is not None

    def test_import_store(self):
        from weclaw.memory.store import StateStore
        assert StateStore is not None

    def test_import_protocol(self):
        from weclaw.claw2claw.protocol import AgentCard, C2CMessage, PeerInfo
        assert AgentCard is not None
        assert C2CMessage is not None
        assert PeerInfo is not None

    def test_import_relay(self):
        from weclaw.claw2claw.relay import RelayClient
        assert RelayClient is not None

    def test_import_terminal_channel(self):
        from weclaw.channel.terminal import TerminalChannel
        assert TerminalChannel is not None

    def test_import_main(self):
        from weclaw.__main__ import main
        assert callable(main)


class TestMessageIntent:
    """测试意图解析数据模型"""

    def test_default_intent(self):
        from weclaw.brain.core import MessageIntent
        intent = MessageIntent(action="general", raw_instruction="测试")
        assert intent.action == "general"
        assert intent.urgency == "normal"
        assert intent.target_name is None

    def test_send_message_intent(self):
        from weclaw.brain.core import MessageIntent
        intent = MessageIntent(
            action="send_message",
            target_name="小王",
            message_gist="问数据好了没",
            raw_instruction="帮我问小王数据好了没",
        )
        assert intent.action == "send_message"
        assert intent.target_name == "小王"


class TestAgentCard:
    """测试龙虾名片"""

    def test_create_agent_card(self):
        from weclaw.claw2claw.protocol import AgentCard, AgentCapability
        card = AgentCard(
            lobster_id="lobster_test123",
            lobster_name="🦞 测试龙虾",
            owner_name="测试者",
            capabilities=[
                AgentCapability(name="relay_message", description="代主人传话")
            ],
        )
        assert card.lobster_id == "lobster_test123"
        assert card.owner_name == "测试者"
        assert len(card.capabilities) == 1

    def test_agent_card_to_dict(self):
        from weclaw.claw2claw.protocol import AgentCard
        card = AgentCard(
            lobster_id="lobster_abc",
            lobster_name="🦞 ABC",
            owner_name="ABC",
        )
        d = card.model_dump()
        assert "lobster_id" in d
        assert d["lobster_id"] == "lobster_abc"


class TestC2CMessage:
    """测试龙虾消息"""

    def test_create_message(self):
        from weclaw.claw2claw.protocol import C2CMessage
        msg = C2CMessage(
            from_lobster_id="lobster_a",
            msg_type="message",
            content="你好！",
        )
        assert msg.from_lobster_id == "lobster_a"
        assert msg.msg_type == "message"
        assert msg.content == "你好！"


class TestStateStore:
    """测试状态持久化"""

    def test_create_store(self, tmp_path):
        from weclaw.memory.store import StateStore
        db_path = str(tmp_path / "test_state.db")
        store = StateStore(db_path=db_path)
        assert store is not None

    def test_settings_kv(self, tmp_path):
        from weclaw.memory.store import StateStore
        db_path = str(tmp_path / "test_state.db")
        store = StateStore(db_path=db_path)

        # 写入设置
        store.set_setting("test_key", "test_value")
        # 读取设置
        value = store.get_setting("test_key")
        assert value == "test_value"

        # 读取不存在的设置
        none_value = store.get_setting("nonexistent")
        assert none_value is None


class TestContactMemory:
    """测试联系人记忆"""

    def test_create_memory(self, tmp_path):
        from weclaw.memory.contacts import ContactMemory
        data_dir = str(tmp_path / "contacts")
        memory = ContactMemory(data_dir=data_dir)
        assert memory is not None
