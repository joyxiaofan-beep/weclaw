"""
brain/core.py — 弃用存根

此模块原为 WeClaw 的 AI 大脑（意图理解、消息生成、回复摘要等）。
自 v1.1.0 起，WeClaw 转型为纯通信协议 SDK，
AI 智能不再是 WeClaw 的职责。

保留内容：
- mask_api_key()：纯字符串工具函数
- MessageIntent / GeneratedMessage / ReplyDigest / ExtractedContactInfo：
  Pydantic 数据模型（向后兼容）
- AICallError / AIParseError：异常类存根

已移除：
- Brain 类（全部 AI 调用逻辑）
- openai / tenacity 依赖
- ContactMemory 依赖

如需 AI 功能，请在外部自行实现并通过 SDK 回调注入。
"""

import warnings
from typing import Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────

def mask_api_key(key: str) -> str:
    """遮蔽 API key，仅显示前4后4字符，用于安全日志输出。

    >>> mask_api_key("sk-abcdef1234567890abcdef1234567890")
    'sk-a***7890'
    >>> mask_api_key("short")
    '****'
    """
    if not key or len(key) <= 8:
        return "****"
    return f"{key[:4]}***{key[-4:]}"


# ──────────────────────────────────────────
# 异常定义（保留向后兼容）
# ──────────────────────────────────────────

class AICallError(Exception):
    """AI 调用失败（已弃用 — WeClaw 不再内置 AI 调用）"""
    pass


class AIParseError(Exception):
    """AI 返回内容解析失败（已弃用）"""
    pass


# ──────────────────────────────────────────
# 数据模型（保留向后兼容）
# ──────────────────────────────────────────

class MessageIntent(BaseModel):
    """用户指令的意图解析"""
    action: str  # "send_message" | "check_reply" | "find_person" | "update_contact" | "general"
    target_name: Optional[str] = None
    message_gist: Optional[str] = None
    topic: Optional[str] = None
    urgency: str = "normal"
    raw_instruction: str = ""


class GeneratedMessage(BaseModel):
    """AI 生成的代发消息"""
    content: str
    tone: str = "casual"
    explanation: str = ""


class ReplyDigest(BaseModel):
    """回复摘要"""
    key_info: str
    action_needed: bool = False
    suggested_response: Optional[str] = None
    extracted_topics: list[str] = Field(default_factory=list)


class ExtractedContactInfo(BaseModel):
    """从对话中提取的联系人信息"""
    name: str
    info_type: str   # "expertise" | "trait" | "role" | "preference" | "note"
    info_value: str


# ──────────────────────────────────────────
# Brain 类弃用存根
# ──────────────────────────────────────────

class Brain:
    """
    已弃用 — WeClaw v1.1.0+ 不再内置 AI 大脑。

    WeClaw 现在是纯通信协议 SDK，AI 智能应由外部实现。
    实例化此类会发出 DeprecationWarning。

    迁移指南：
    - 意图解析：在外部使用你的 LLM 实现
    - 消息生成：通过 @claw.on_message 回调处理
    - 回复摘要：在回调中自行调用 LLM
    - 联系人学习：使用 ContactMemory 的基础 CRUD 方法
    """

    def __init__(self, *args, **kwargs):
        warnings.warn(
            "Brain 类已弃用。WeClaw v1.1.0+ 是纯通信协议 SDK，"
            "不再内置 AI 大脑。请在外部实现 AI 逻辑并通过回调注入。",
            DeprecationWarning,
            stacklevel=2,
        )

    def parse_intent(self, user_input: str) -> MessageIntent:
        """已弃用 — 始终返回 general 意图"""
        warnings.warn("Brain.parse_intent() 已弃用", DeprecationWarning, stacklevel=2)
        return MessageIntent(action="general", raw_instruction=user_input)

    def compose_message(self, intent, sender_style: str = "auto"):
        """已弃用 — 始终返回 None"""
        warnings.warn("Brain.compose_message() 已弃用", DeprecationWarning, stacklevel=2)
        return None

    def digest_reply(self, from_name: str, reply_content: str, context: str = ""):
        """已弃用 — 返回原文作为摘要"""
        warnings.warn("Brain.digest_reply() 已弃用", DeprecationWarning, stacklevel=2)
        return ReplyDigest(key_info=reply_content[:200], action_needed=False)

    def extract_contact_info(self, conversation: str):
        """已弃用 — 始终返回空列表"""
        warnings.warn("Brain.extract_contact_info() 已弃用", DeprecationWarning, stacklevel=2)
        return []

    def apply_learned_info(self, infos):
        """已弃用 — 始终返回空列表"""
        warnings.warn("Brain.apply_learned_info() 已弃用", DeprecationWarning, stacklevel=2)
        return []

    def generate_ai_summary(self, name: str):
        """已弃用 — 始终返回 None"""
        warnings.warn("Brain.generate_ai_summary() 已弃用", DeprecationWarning, stacklevel=2)
        return None

    def score_peer_match(self, my_card, peer_summary: dict) -> float:
        """已弃用 — 始终返回 0.0"""
        warnings.warn("Brain.score_peer_match() 已弃用", DeprecationWarning, stacklevel=2)
        return 0.0
