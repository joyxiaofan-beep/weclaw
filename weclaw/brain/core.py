"""
AI 大脑 — 龙虾的核心智能

负责：
1. 理解用户意图（要联系谁？做什么？）
2. 生成代发消息（根据对方画像调整风格）
3. 提炼收到的回复
4. 从交互中提取人脉信息

v0.2 新增：
- 对话上下文注入（每次 AI 调用都带最近 N 轮对话）
- 重试机制（tenacity 指数退避）
- 降级保护（AI 失败时不发送垃圾消息，改为通知主人）
"""

import json
from typing import Optional

from openai import OpenAI
from loguru import logger
from pydantic import BaseModel, Field
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
import logging

from weclaw.memory.contacts import ContactMemory

# tenacity 日志桥接
_tenacity_logger = logging.getLogger("tenacity.retry")


# ──────────────────────────────────────────
# 异常定义
# ──────────────────────────────────────────

class AICallError(Exception):
    """AI 调用失败（可重试的）"""
    pass


class AIParseError(Exception):
    """AI 返回内容解析失败（不重试，走降级）"""
    pass


# ──────────────────────────────────────────
# 意图解析结果
# ──────────────────────────────────────────

class MessageIntent(BaseModel):
    """用户指令的意图解析"""
    action: str  # "send_message" | "check_reply" | "find_person" | "update_contact" | "general"
    target_name: Optional[str] = None       # 要联系的人
    message_gist: Optional[str] = None      # 消息要点
    topic: Optional[str] = None             # 相关话题
    urgency: str = "normal"                 # "low" | "normal" | "high"
    raw_instruction: str = ""               # 原始指令


class GeneratedMessage(BaseModel):
    """AI 生成的代发消息"""
    content: str                  # 消息内容
    tone: str = "casual"          # 语气：formal / casual / friendly
    explanation: str = ""         # 为什么这样写（给用户看的）


class ReplyDigest(BaseModel):
    """回复摘要"""
    key_info: str                 # 关键信息
    action_needed: bool = False   # 是否需要你进一步操作
    suggested_response: Optional[str] = None  # 建议的回复
    extracted_topics: list[str] = Field(default_factory=list)  # 提取的话题


class ExtractedContactInfo(BaseModel):
    """从对话中提取的联系人信息"""
    name: str
    info_type: str   # "expertise" | "trait" | "role" | "preference" | "note"
    info_value: str


# ──────────────────────────────────────────
# AI 大脑
# ──────────────────────────────────────────

class Brain:
    """龙虾的大脑"""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
        contact_memory: Optional[ContactMemory] = None,
        conversation_context_fn=None,
    ):
        """
        Args:
            api_key: LLM API Key
            model: 模型名称
            base_url: 自定义 API 地址
            contact_memory: 人脉记忆
            conversation_context_fn: 获取对话上下文的回调函数
                                     签名: () -> str，返回格式化的上下文字符串
        """
        # 检查国产模型是否遗漏了 base_url
        if base_url is None and api_key:
            # DeepSeek 等国产模型的 key 格式也是 sk- 开头，无法自动区分
            # 但如果模型名包含 deepseek/moonshot/qwen 等关键词，提示用户
            model_lower = model.lower()
            base_url_hints = {
                "deepseek": "https://api.deepseek.com/v1",
                "moonshot": "https://api.moonshot.cn/v1",
                "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "glm": "https://open.bigmodel.cn/api/paas/v4",
            }
            for keyword, hint_url in base_url_hints.items():
                if keyword in model_lower:
                    logger.warning(
                        f"检测到模型名含 '{keyword}' 但未设置 base_url，"
                        f"将自动使用 {hint_url}。"
                        f"如需修改，请在 config.yaml 中设置 ai.base_url"
                    )
                    base_url = hint_url
                    break

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.memory = contact_memory or ContactMemory()
        self._get_context = conversation_context_fn

    def _get_conversation_context(self) -> str:
        """获取对话上下文"""
        if self._get_context:
            try:
                return self._get_context()
            except Exception as e:
                logger.warning(f"获取对话上下文失败: {e}")
        return "（暂无对话历史）"

    @retry(
        retry=retry_if_exception_type(AICallError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        before_sleep=before_sleep_log(_tenacity_logger, logging.WARNING),
        reraise=True,
    )
    def _chat(self, system_prompt: str, user_msg: str) -> str:
        """
        调用 AI 模型（带重试）

        重试策略：最多 3 次，指数退避 1s → 2s → 4s
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
        }

        try:
            resp = self.client.chat.completions.create(**kwargs)
            result = resp.choices[0].message.content
            if not result:
                raise AICallError("AI 返回空内容")
            return result
        except AICallError:
            raise
        except Exception as e:
            logger.warning(f"AI 调用异常: {type(e).__name__}: {e}")
            raise AICallError(f"AI 调用失败: {e}") from e

    def _safe_parse_json(self, result: str) -> dict:
        """安全地解析 AI 返回的 JSON"""
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(result)

    # ──────────────────────────────────────────
    # 1. 意图理解
    # ──────────────────────────────────────────

    def parse_intent(self, user_input: str) -> MessageIntent:
        """
        理解用户指令的意图

        v0.2: 注入对话上下文，让龙虾理解"他""那个""上次"等指代
        """
        contacts_brief = self.memory.get_all_contacts_brief()
        conversation_context = self._get_conversation_context()

        system_prompt = f"""你是一个社交代理的意图解析器。
用户会给你一个指令，你需要判断他想做什么。

## 已知联系人
{contacts_brief}

## 最近对话上下文
{conversation_context}

## 重要规则
1. 如果用户说"他""她""那个人"等指代词，根据上下文推断指的是谁
2. 如果用户说"上次""刚才""那件事"，根据上下文推断具体内容
3. "发送N"/"取消N" 是确认/取消操作指令，action 应该是 "confirm_send" / "cancel_send"
4. "改N 内容" 是修改后发送，action 应该是 "edit_send"
5. "待办"/"跟进"/"超时"/"催"/"提醒" 是查看待跟进事项，action 应该是 "check_reminders"
6. 如果用户说"龙虾传话""让XX的龙虾""给XX的龙虾说"等龙虾间通信指令，action 应该是 "c2c_relay"
7. 如果用户说"龙虾握手""连接XX的龙虾"等，action 应该是 "c2c_handshake"
8. 如果用户说"龙虾通讯录""其他龙虾""龙虾列表"等，action 应该是 "c2c_list_peers"

## 输出格式
返回 JSON：
{{
    "action": "send_message | check_reply | find_person | update_contact | confirm_send | cancel_send | edit_send | check_reminders | c2c_relay | c2c_handshake | c2c_list_peers | general",
    "target_name": "联系人称呼或null",
    "message_gist": "要传达的信息要点或null",
    "topic": "相关话题或null",
    "urgency": "low | normal | high"
}}

只返回 JSON，不要其他内容。"""

        try:
            result = self._chat(system_prompt, user_input)
            data = self._safe_parse_json(result)
            return MessageIntent(raw_instruction=user_input, **data)
        except AICallError:
            logger.error("意图解析: AI 调用多次重试后仍失败")
            return MessageIntent(
                action="general",
                raw_instruction=user_input,
            )
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"意图解析 JSON 失败，回退到 general: {type(e).__name__}")
            return MessageIntent(
                action="general",
                raw_instruction=user_input,
            )

    # ──────────────────────────────────────────
    # 2. 生成代发消息
    # ──────────────────────────────────────────

    def compose_message(
        self,
        intent: MessageIntent,
        sender_style: str = "auto",
    ) -> Optional[GeneratedMessage]:
        """
        根据意图和对方画像，生成代发消息

        v0.2: 注入对话上下文 + AI 失败时返回 None（不再原样转发）

        Returns:
            GeneratedMessage 或 None（AI 失败时）
        """
        # 获取对方画像
        contact_brief = "（新联系人，没有历史记录）"
        if intent.target_name:
            contact_brief = self.memory.get_contact_brief(intent.target_name)

        conversation_context = self._get_conversation_context()

        system_prompt = f"""你是一个代替用户发消息的助手。
根据用户的意图和对方的画像，生成一条合适的消息。

## 对方信息
{contact_brief}

## 最近对话上下文
{conversation_context}

## 要求
1. 消息应该自然、得体，像是用户本人写的
2. 根据对方的沟通特征调整风格
3. 不要太正式也不要太随意，除非有明确偏好
4. 简洁明了，不要废话
5. 风格偏好: {sender_style}
6. 参考对话上下文，确保消息内容连贯（比如"帮我跟他确认一下"要知道确认什么）

## 输出格式
返回 JSON：
{{
    "content": "消息内容",
    "tone": "formal | casual | friendly",
    "explanation": "简短说明为什么这样写"
}}

只返回 JSON。"""

        user_msg = f"用户想说的要点：{intent.message_gist or intent.raw_instruction}"

        try:
            result = self._chat(system_prompt, user_msg)
            data = self._safe_parse_json(result)
            return GeneratedMessage(**data)
        except AICallError:
            logger.error("消息生成: AI 调用多次重试后仍失败")
            return None  # 返回 None，由调用方决定如何告知用户
        except Exception as e:
            logger.warning(f"消息生成解析失败: {type(e).__name__}")
            return None

    # ──────────────────────────────────────────
    # 3. 回复摘要
    # ──────────────────────────────────────────

    def digest_reply(
        self,
        from_name: str,
        reply_content: str,
        context: str = "",
    ) -> ReplyDigest:
        """
        提炼对方的回复，提取关键信息

        v0.2: 自动注入对话上下文
        """
        contact_brief = self.memory.get_contact_brief(from_name)
        conversation_context = self._get_conversation_context()

        # 合并外部传入的 context 和自动获取的上下文
        full_context = conversation_context
        if context:
            full_context = f"{context}\n\n---\n{conversation_context}"

        system_prompt = f"""你是一个社交代理的回复分析器。
有人回复了消息，你需要提炼关键信息告诉用户。

## 回复者信息
{contact_brief}

## 之前的对话上下文
{full_context}

## 输出格式
返回 JSON：
{{
    "key_info": "关键信息摘要（一两句话）",
    "action_needed": true/false,
    "suggested_response": "建议的回复或null",
    "extracted_topics": ["话题1", "话题2"]
}}

只返回 JSON。"""

        try:
            result = self._chat(system_prompt, f"{from_name} 说：{reply_content}")
            data = self._safe_parse_json(result)
            return ReplyDigest(**data)
        except AICallError:
            logger.error("回复摘要: AI 调用多次重试后仍失败")
            return ReplyDigest(
                key_info=f"[AI暂时不可用] {from_name} 的消息: {reply_content[:100]}",
                action_needed=True,  # 保守起见标记需要跟进
            )
        except Exception as e:
            logger.warning(f"回复摘要解析失败: {type(e).__name__}")
            return ReplyDigest(
                key_info=reply_content[:200],
                action_needed=False,
            )

    # ──────────────────────────────────────────
    # 4. 对话学习 — 从交互中提取人脉信息
    # ──────────────────────────────────────────

    def extract_contact_info(self, conversation: str) -> list[ExtractedContactInfo]:
        """
        从一段对话中提取联系人相关信息

        这是龙虾"学习"的核心——每次交互后调用，
        自动积累对每个人的认知。
        """
        system_prompt = """你是一个信息提取器。
从对话中提取提到的联系人信息。

## 提取类型
- expertise: 某人擅长什么
- trait: 某人的沟通/行为特征
- role: 某人的职位/角色
- preference: 某人的偏好
- note: 其他值得记住的信息

## 输出格式
返回 JSON 数组：
[
    {"name": "小王", "info_type": "expertise", "info_value": "数据分析"},
    {"name": "Lucy", "info_type": "role", "info_value": "UX 设计 lead"}
]

如果没有可提取的信息，返回空数组 []。
只返回 JSON。"""

        try:
            result = self._chat(system_prompt, conversation)
            data = self._safe_parse_json(result)
            return [ExtractedContactInfo(**item) for item in data]
        except AICallError:
            logger.error("联系人信息提取: AI 调用多次重试后仍失败")
            return []
        except Exception as e:
            logger.warning(f"联系人信息提取失败: {type(e).__name__}")
            return []

    def apply_learned_info(self, infos: list[ExtractedContactInfo]) -> list[ExtractedContactInfo]:
        """
        将学到的信息应用到人脉记忆中

        Returns:
            实际新增的信息列表（去重后的，可用于轻提示）
        """
        new_infos: list[ExtractedContactInfo] = []

        for info in infos:
            profile = self.memory.get_or_create_contact(info.name)
            is_new = False

            if info.info_type == "expertise":
                if info.info_value not in profile.expertise:
                    profile.expertise.append(info.info_value)
                    is_new = True
            elif info.info_type == "trait":
                if info.info_value not in profile.traits:
                    profile.traits.append(info.info_value)
                    is_new = True
            elif info.info_type == "role":
                if profile.title != info.info_value:
                    profile.title = info.info_value
                    is_new = True
            elif info.info_type == "preference":
                note = f"偏好: {info.info_value}"
                if note not in profile.notes:
                    profile.notes.append(note)
                    is_new = True
            elif info.info_type == "note":
                if info.info_value not in profile.notes:
                    profile.notes.append(info.info_value)
                    is_new = True

            if is_new:
                new_infos.append(info)

            self.memory._save_contact(profile)
            logger.info(f"学到新信息: {info.name} - {info.info_type} (新增: {is_new})")

        return new_infos

    # ──────────────────────────────────────────
    # 5. AI 综合画像生成
    # ──────────────────────────────────────────

    def generate_ai_summary(self, name: str) -> Optional[str]:
        """
        为联系人生成 AI 综合画像摘要

        基于该联系人的所有已知信息（基本信息、expertise、traits、
        notes、交互历史）生成一段简洁的画像描述。

        Returns:
            生成的画像摘要字符串，失败返回 None
        """
        profile = self.memory.get_contact(name)
        if not profile:
            logger.warning(f"AI Summary: 联系人 {name} 不存在")
            return None

        # 构建画像数据
        info_lines = []
        if profile.real_name:
            info_lines.append(f"真名: {profile.real_name}")
        if profile.department:
            info_lines.append(f"部门: {profile.department}")
        if profile.title:
            info_lines.append(f"职级/角色: {profile.title}")
        if profile.expertise:
            info_lines.append(f"擅长: {', '.join(profile.expertise)}")
        if profile.traits:
            info_lines.append(f"沟通特征: {', '.join(profile.traits)}")
        if profile.notes:
            info_lines.append(f"备注: {'; '.join(profile.notes)}")

        # 最近交互摘要
        recent = profile.interactions[-10:]
        interaction_lines = []
        for i in recent:
            arrow = "→" if i.direction == "outgoing" else "←"
            interaction_lines.append(f"  {arrow} [{i.time[:10]}] {i.summary}")

        profile_text = "\n".join(info_lines) if info_lines else "（暂无基本信息）"
        interactions_text = "\n".join(interaction_lines) if interaction_lines else "（暂无交互记录）"

        system_prompt = """你是一个社交代理的画像生成器。
根据一个联系人的所有已知信息，生成一段简洁的综合画像摘要。

## 要求
1. 用 2-4 句话概括这个人的核心特征
2. 包含：角色/专长、沟通风格、关键印象
3. 要有观点和判断，不要只是罗列信息
4. 语气像是一个好助手私下跟你说的
5. 如果信息很少，诚实说明，不要编造

直接输出画像文本，不要任何格式标记。"""

        user_msg = f"""联系人: {profile.name}
总交互次数: {profile.total_interactions}
首次接触: {profile.first_contact or '未知'}
最近联系: {profile.last_contact or '未知'}

## 已知信息
{profile_text}

## 最近交互
{interactions_text}"""

        try:
            summary = self._chat(system_prompt, user_msg)
            summary = summary.strip()
            logger.info(f"AI Summary 生成成功: {name} ({len(summary)} 字)")
            return summary
        except Exception as e:
            logger.error(f"AI Summary 生成失败 ({name}): {e}")
            return None

    # ──────────────────────────────────────────
    # v0.8: Peer Match Scoring（主动发现评分）
    # ──────────────────────────────────────────

    def score_peer_match(self, my_card, peer_summary: dict) -> float:
        """
        评估一只发现的龙虾与自己的匹配程度（0.0 ~ 1.0）

        用 AI 综合分析双方的标签、服务、兴趣和行业，
        返回一个浮点数表示匹配度。用于主动发现推荐排序。

        Args:
            my_card: 自己的 AgentCard（协议层对象）
            peer_summary: 对方的发现摘要 dict，包含 lobster_name, tags,
                          description, services_offered, interests, industries 等

        Returns:
            0.0 ~ 1.0 的匹配分，出错时返回 0.0
        """
        system_prompt = """你是一个人脉匹配评分引擎。
给定两只"龙虾"（AI 社交代理）的档案信息，评估它们的互补匹配度。

## 评分维度（权重）
1. **标签重叠**（30%）: 双方 tags 有多少交集
2. **服务互补**（25%）: 一方提供的服务是否是另一方需要的
3. **兴趣交叉**（20%）: 兴趣领域的相似度
4. **行业相关**（15%）: 是否在相近行业
5. **地区匹配**（10%）: 是否在同一地区

## 输出格式
只输出一个 JSON 对象：
{"score": 0.75, "reason": "一句话说明匹配理由"}

score 必须是 0.0 到 1.0 的浮点数。"""

        my_info = {
            "name": my_card.lobster_name,
            "tags": my_card.tags,
            "description": getattr(my_card, "description", ""),
            "services_offered": getattr(my_card, "services_offered", []),
            "services_needed": getattr(my_card, "services_needed", []),
            "interests": getattr(my_card, "interests", []),
            "industries": getattr(my_card, "industries", []),
            "location_area": getattr(my_card, "location_area", ""),
        }

        user_msg = f"""## 我方龙虾
{json.dumps(my_info, ensure_ascii=False, indent=2)}

## 对方龙虾
{json.dumps(peer_summary, ensure_ascii=False, indent=2)}

请评分："""

        try:
            raw = self._chat(system_prompt, user_msg)
            result = self._safe_parse_json(raw)
            score = float(result.get("score", 0.0))
            reason = result.get("reason", "")
            score = max(0.0, min(1.0, score))
            logger.info(
                f"🎯 Peer Match: {peer_summary.get('lobster_name', '?')} "
                f"→ {score:.2f} ({reason})"
            )
            return score
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Peer Match 评分解析失败: {e}")
            return 0.0
        except Exception as e:
            logger.error(f"Peer Match 评分调用失败: {e}")
            return 0.0
