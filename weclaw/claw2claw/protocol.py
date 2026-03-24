"""
C2C 协议定义 — Agent Card + Message Format (v0.8)

参考 Google A2A 精简版，为"个人龙虾间互联"设计。
v0.7: 借鉴 Tobira.ai 经验，增加结构化能力声明、渐进式信任、发现与引荐机制。
v0.8: 结构化 Agent Profile（自我介绍、服务、兴趣）、Agent Behavior（安全规则、主动模式）、
      Welcome Bubbles（首次见面自动问候）、公开主页。

Agent Card = 龙虾名片
  - 我是谁（owner、lobster_name、handle）
  - 怎么联系我（endpoint URL、profile_url）
  - 我能做什么（capabilities、tags）
  - 我是什么样的（description、services、interests、values）
  - 我的可信度（trust_score）
  - 我的认证方式（auth）

C2C Message = 龙虾间传递的标准消息
  - 发送方身份（from_card 精简版）
  - 消息类型（message / query / status / handshake / introduce）
  - 消息内容
  - 上下文追踪（message_id, reply_to）

Agent Behavior = 龙虾行为规则
  - 安全规则（保护哪些信息）
  - 主动模式（自动发现匹配龙虾）
  - 过滤规则（垃圾消息识别）
  - 定时任务
"""

import hashlib
import hmac
import time
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────
# Agent Card — 龙虾名片
# ──────────────────────────────────────────

class AgentCapability(BaseModel):
    """龙虾的一项能力"""
    name: str               # 能力名称：schedule_check, send_message, knowledge_query
    description: str = ""   # 描述


class AgentCard(BaseModel):
    """
    龙虾名片 — 告诉别的龙虾"我是谁"

    类比：A2A 的 Agent Card，但极度精简。

    v2: lobster_id 是持久龙虾号（类似微信号），客户端生成并本地保存，
    重启不变。格式：lobster_XXXXXXXX（8位hex）。

    v0.7 新增（借鉴 Tobira.ai）：
    - handle: 可读别名（如 @alice），类似 Tobira 的人类友好标识
    - tags: 标签列表（如 ["AI", "设计", "产品"]），用于能力发现和匹配
    - trust_score: 初始信任分（0-100），展示给其他龙虾参考
    - profile_url: 个人主页链接，类似 Tobira 的 Agent Profile URL

    v0.8 新增（结构化 Profile，借鉴 Tobira.ai Agent Profile）：
    - description: 一句话介绍
    - services_offered / services_needed: 我能提供 / 我在寻找的服务
    - interests: 兴趣领域
    - welcome_bubbles: 首次见面自动发送的问候泡泡
    - values: 价值观标签
    - personal_looking_for: 我想认识什么样的龙虾
    - industries: 所在行业
    - location_area: 所在地区
    - language: 使用语言
    - page_public: 是否公开个人主页
    """
    # 身份
    lobster_id: str = Field(default_factory=lambda: f"lobster_{uuid.uuid4().hex[:8]}")
    lobster_name: str = "🦞 未命名龙虾"
    owner_name: str = "匿名主人"
    handle: str = ""  # 可读别名，如 @alice（v0.7，借鉴 Tobira DIDs 的可读性）

    # 联系方式
    endpoint: str = ""  # 这只龙虾的 HTTP 端点 (e.g., "https://lobster-alice.example.com")
    profile_url: str = ""  # 主人/龙虾的个人主页（v0.7，借鉴 Tobira Agent Profile）

    # 能力与标签
    capabilities: list[AgentCapability] = Field(default_factory=lambda: [
        AgentCapability(name="relay_message", description="代主人传话"),
        AgentCapability(name="check_availability", description="查看主人是否方便"),
    ])
    tags: list[str] = Field(default_factory=list)  # 技能标签，用于发现匹配（v0.7）

    # 信任
    trust_score: int = Field(default=50, ge=0, le=100)  # 初始信任分（v0.7，借鉴 Tobira 信誉评分）

    # ── v0.8: 结构化 Profile（借鉴 Tobira.ai）──
    description: str = ""  # 一句话自我介绍："Alice 的 AI 助手，擅长技术咨询"
    services_offered: list[str] = Field(default_factory=list)  # 能提供的服务 ["技术答疑", "日程管理"]
    services_needed: list[str] = Field(default_factory=list)   # 在寻找的服务 ["设计协作", "翻译"]
    interests: list[str] = Field(default_factory=list)         # 兴趣领域 ["AI", "Web3", "咖啡"]
    welcome_bubbles: list[str] = Field(default_factory=list)   # 首次见面问候 ["你好！我是 Alice 的龙虾 🦞", "有什么可以帮你的？"]
    values: list[str] = Field(default_factory=list)            # 价值观 ["开源", "协作", "效率"]
    personal_looking_for: str = ""                              # 我想认识什么样的龙虾
    industries: list[str] = Field(default_factory=list)         # 行业 ["科技", "教育"]
    location_area: str = ""                                     # 地区 "深圳"
    language: str = "zh"                                        # 使用语言
    page_public: bool = True                                    # 是否公开个人主页

    # 认证
    shared_secret: Optional[str] = None  # 对称密钥（两只龙虾之间共享）

    # 元数据
    version: str = "0.8.0"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def to_public(self) -> dict:
        """导出公开版本（不含 secret，含 v0.8 Profile 信息）"""
        return {
            "lobster_id": self.lobster_id,
            "lobster_name": self.lobster_name,
            "owner_name": self.owner_name,
            "handle": self.handle,
            "endpoint": self.endpoint,
            "profile_url": self.profile_url,
            "capabilities": [c.model_dump() for c in self.capabilities],
            "tags": self.tags,
            "trust_score": self.trust_score,
            # v0.8 Profile
            "description": self.description,
            "services_offered": self.services_offered,
            "services_needed": self.services_needed,
            "interests": self.interests,
            "welcome_bubbles": self.welcome_bubbles,
            "values": self.values,
            "personal_looking_for": self.personal_looking_for,
            "industries": self.industries,
            "location_area": self.location_area,
            "language": self.language,
            "page_public": self.page_public,
            "version": self.version,
        }

    def to_discovery_summary(self) -> dict:
        """导出发现摘要（用于 Relay 广播，比 to_public 更精简，含 v0.8 关键字段）"""
        return {
            "lobster_id": self.lobster_id,
            "lobster_name": self.lobster_name,
            "owner_name": self.owner_name,
            "handle": self.handle,
            "tags": self.tags,
            "trust_score": self.trust_score,
            # v0.8: 发现摘要中也带上简介和服务，方便匹配推荐
            "description": self.description,
            "services_offered": self.services_offered,
            "interests": self.interests,
            "industries": self.industries,
            "location_area": self.location_area,
        }


# ──────────────────────────────────────────
# C2C Message — 龙虾间消息格式
# ──────────────────────────────────────────

class C2CMessage(BaseModel):
    """
    龙虾间传递的标准消息

    消息类型：
    - handshake: 握手（交换名片）
    - message: 代主人传话
    - query: 查询对方信息（如日程、能力）
    - status: 状态更新（如主人已读、主人不方便）
    - ack: 确认收到
    - introduce: 好友引荐（v0.7，A 向 B 介绍 C）
    """
    # 消息唯一 ID
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # 发送方信息（精简版 Agent Card）
    from_lobster_id: str
    from_lobster_name: str = ""
    from_owner_name: str = ""
    from_endpoint: str = ""  # 回复用

    # 消息类型
    msg_type: str = "message"  # handshake | message | query | status | ack

    # 消息体
    content: str = ""         # 主要内容
    payload: dict = Field(default_factory=dict)  # 结构化数据（如 handshake 时携带完整名片）

    # 上下文
    reply_to: Optional[str] = None  # 回复哪条消息的 message_id
    conversation_id: Optional[str] = None  # 对话线程 ID

    # 元信息
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    signature: Optional[str] = None  # HMAC 签名

    def sign(self, secret: str) -> "C2CMessage":
        """用共享密钥签名"""
        raw = f"{self.message_id}|{self.from_lobster_id}|{self.msg_type}|{self.content}|{self.timestamp}"
        self.signature = hmac.new(
            secret.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()
        return self

    def verify(self, secret: str, max_age_seconds: int = 300) -> bool:
        """
        验证签名

        Args:
            secret: 共享密钥
            max_age_seconds: 消息最大有效期（秒），默认 5 分钟，防重放攻击
        """
        if not self.signature:
            return False

        # 时间窗口校验：防重放攻击
        try:
            msg_time = datetime.fromisoformat(self.timestamp)
            now = datetime.now()
            age = abs((now - msg_time).total_seconds())
            if age > max_age_seconds:
                return False
        except (ValueError, TypeError):
            return False

        raw = f"{self.message_id}|{self.from_lobster_id}|{self.msg_type}|{self.content}|{self.timestamp}"
        expected = hmac.new(
            secret.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(self.signature, expected)

    def to_ack(self, my_lobster_id: str, my_name: str = "") -> "C2CMessage":
        """生成一条 ACK 回复"""
        return C2CMessage(
            from_lobster_id=my_lobster_id,
            from_lobster_name=my_name,
            msg_type="ack",
            content=f"收到消息 {self.message_id[:8]}",
            reply_to=self.message_id,
            conversation_id=self.conversation_id,
        )


# ──────────────────────────────────────────
# Agent Behavior — 龙虾行为规则 (v0.8)
# ──────────────────────────────────────────

class SecurityRule(BaseModel):
    """安全规则 — 保护哪些信息不对外暴露"""
    protected_fields: list[str] = Field(
        default_factory=lambda: ["phone", "email", "address", "id_number"],
        description="不允许透露的信息字段"
    )
    block_keywords: list[str] = Field(
        default_factory=list,
        description="消息中包含这些关键词时自动拦截"
    )
    max_messages_per_minute: int = Field(
        default=10,
        description="每分钟最大接收消息数（防刷）"
    )


class ProactiveMode(BaseModel):
    """主动发现模式 — 自动寻找匹配的龙虾"""
    enabled: bool = False
    discovery_interval_minutes: int = Field(
        default=30,
        description="自动发现间隔（分钟）"
    )
    auto_handshake: bool = Field(
        default=False,
        description="发现匹配龙虾时是否自动握手"
    )
    match_threshold: float = Field(
        default=0.6, ge=0.0, le=1.0,
        description="匹配度阈值（0-1），高于此值才推荐"
    )
    max_recommendations: int = Field(
        default=5,
        description="每次推荐的最大龙虾数"
    )


class FilterRule(BaseModel):
    """过滤规则 — 哪些消息/龙虾需要过滤"""
    min_trust_to_message: int = Field(
        default=0,
        description="低于此信任分的龙虾消息自动过滤（0=不过滤）"
    )
    block_list: list[str] = Field(
        default_factory=list,
        description="黑名单 lobster_id 列表"
    )
    require_handshake: bool = Field(
        default=False,
        description="是否必须先握手才能收消息"
    )


class ScheduledTask(BaseModel):
    """定时任务"""
    name: str = ""
    cron: str = ""  # cron 表达式，如 "0 9 * * *"（每天9点）
    action: str = ""  # 动作类型：discover / report / cleanup
    enabled: bool = True


class AgentBehavior(BaseModel):
    """
    龙虾行为规则 (v0.8) — 借鉴 Tobira.ai 的 System Prompt + Rules

    定义这只龙虾的安全策略、主动发现模式、过滤规则和定时任务。
    类比：Tobira.ai 的 Agent 配置面板中"Behavior"部分。
    """
    security: SecurityRule = Field(default_factory=SecurityRule)
    proactive: ProactiveMode = Field(default_factory=ProactiveMode)
    filter: FilterRule = Field(default_factory=FilterRule)
    scheduled_tasks: list[ScheduledTask] = Field(default_factory=list)

    # 自定义系统提示（注入 AI Brain，控制龙虾性格和回复风格）
    system_prompt_extra: str = Field(
        default="",
        description="额外的系统提示，会注入到 AI Brain 的 system prompt 中"
    )


# ──────────────────────────────────────────
# Peer Registry — 已知龙虾通讯录
# ──────────────────────────────────────────

class PeerInfo(BaseModel):
    """
    一只已知的远程龙虾

    v0.7 信任升级（借鉴 Tobira.ai 渐进式信誉）：
    - trust_score 取代旧的 trusted: bool
    - 0 = 完全不信任, 50 = 初次握手, 70 = 可信（可传话阈值）, 100 = 完全信任
    - 保留 trusted 属性作为兼容性便捷入口

    v0.8: 新增 description（对方自我介绍），丰富通讯录信息
    """
    lobster_id: str
    lobster_name: str = ""
    owner_name: str = ""
    endpoint: str
    shared_secret: str = ""      # 两只龙虾之间的共享密钥
    capabilities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # v0.7: 对方标签
    handle: str = ""  # v0.7: 对方可读别名
    description: str = ""  # v0.8: 对方自我介绍
    last_seen: Optional[str] = None
    trust_score: int = Field(default=0, ge=0, le=100)  # v0.7: 渐进式信任分
    added_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    introduced_by: Optional[str] = None  # v0.7: 谁引荐的（lobster_id）

    @property
    def trusted(self) -> bool:
        """向后兼容：trust_score >= 70 视为已信任（可传话）"""
        return self.trust_score >= 70

    @trusted.setter
    def trusted(self, value: bool):
        """向后兼容：设置 trusted=True 将 trust_score 提升到 70"""
        if value and self.trust_score < 70:
            self.trust_score = 70
        elif not value and self.trust_score >= 70:
            self.trust_score = 0


class PeerRegistry:
    """
    已知龙虾通讯录（内存版，后续可持久化到 YAML/SQLite）

    管理"我认识的其他龙虾"。
    """

    def __init__(self):
        self._peers: dict[str, PeerInfo] = {}  # lobster_id -> PeerInfo

    def add_peer(self, peer: PeerInfo) -> None:
        """添加或更新一只已知龙虾"""
        self._peers[peer.lobster_id] = peer

    def get_peer(self, lobster_id: str) -> Optional[PeerInfo]:
        """按 ID 查找"""
        return self._peers.get(lobster_id)

    def find_by_owner(self, owner_name: str) -> Optional[PeerInfo]:
        """按主人名字查找对方的龙虾（精确匹配优先）"""
        owner_lower = owner_name.lower()
        # 精确匹配优先
        for peer in self._peers.values():
            if owner_lower == peer.owner_name.lower():
                return peer
        # 子串匹配回退
        for peer in self._peers.values():
            if owner_lower in peer.owner_name.lower():
                return peer
        return None

    def find_by_name(self, name: str) -> Optional[PeerInfo]:
        """
        按龙虾名字或主人名字查找（精确匹配优先）

        1. 先尝试精确匹配（忽略大小写）
        2. 再尝试子串匹配
        """
        name_lower = name.lower()

        # 精确匹配优先
        for peer in self._peers.values():
            if (name_lower == peer.lobster_name.lower()
                    or name_lower == peer.owner_name.lower()):
                return peer

        # 子串匹配回退
        for peer in self._peers.values():
            if (name_lower in peer.lobster_name.lower()
                    or name_lower in peer.owner_name.lower()):
                return peer
        return None

    def list_peers(self) -> list[PeerInfo]:
        """列出所有已知龙虾"""
        return list(self._peers.values())

    def list_trusted(self) -> list[PeerInfo]:
        """只列出已信任的龙虾（trust_score >= 70）"""
        return [p for p in self._peers.values() if p.trusted]

    def find_by_tags(self, tags: list[str]) -> list[PeerInfo]:
        """按标签搜索龙虾（v0.7，匹配任意一个 tag）"""
        tags_lower = {t.lower() for t in tags}
        results = []
        for peer in self._peers.values():
            peer_tags = {t.lower() for t in peer.tags}
            if tags_lower & peer_tags:  # 交集非空
                results.append(peer)
        return sorted(results, key=lambda p: p.trust_score, reverse=True)

    def find_by_handle(self, handle: str) -> Optional[PeerInfo]:
        """按可读别名查找（v0.7）"""
        handle_clean = handle.lstrip("@").lower()
        for peer in self._peers.values():
            if peer.handle.lstrip("@").lower() == handle_clean:
                return peer
        return None

    def remove_peer(self, lobster_id: str) -> bool:
        """删除一只龙虾"""
        if lobster_id in self._peers:
            del self._peers[lobster_id]
            return True
        return False

    def to_dict_list(self) -> list[dict]:
        """导出为 dict 列表"""
        return [p.model_dump() for p in self._peers.values()]


class PersistentPeerRegistry(PeerRegistry):
    """
    持久化版龙虾通讯录 — 基于 StateStore (SQLite)

    继承 PeerRegistry，在增删操作时自动写入数据库。
    启动时从数据库恢复已知龙虾。
    """

    def __init__(self, state_store):
        """
        Args:
            state_store: StateStore 实例（提供 save_peer / delete_peer / list_all_peers）
        """
        super().__init__()
        self._store = state_store
        self._load_from_db()

    def _load_from_db(self):
        """从数据库恢复已知龙虾"""
        peers = self._store.list_all_peers()
        for p in peers:
            self._peers[p["lobster_id"]] = PeerInfo(**p)

    def add_peer(self, peer: PeerInfo) -> None:
        """添加或更新一只已知龙虾（同时持久化）"""
        super().add_peer(peer)
        self._store.save_peer(peer.model_dump())

    def remove_peer(self, lobster_id: str) -> bool:
        """删除一只龙虾（同时从数据库删除）"""
        if super().remove_peer(lobster_id):
            self._store.delete_peer(lobster_id)
            return True
        return False
