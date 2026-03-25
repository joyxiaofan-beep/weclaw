# Changelog

All notable changes to WeClaw will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.2] - 2026-03-25

### 🛡️ P1 安全加固 — 7 项安全改进

#### Security

- **P1-1: 强制签名验证** — 已知龙虾无 `shared_secret` 时，拒绝所有非 `handshake` 消息（发送端 `client.py` + 接收端 `handler.py` 双重拦截），杜绝明文消息传输
- **P1-2 + P1-6: 密钥协商机制** — 加好友成功时 Relay Server 自动生成 256-bit 密码学安全 `shared_secret`（`secrets.token_hex(32)`），通过 `friend_added` 消息分发给双方，客户端自动保存到本地通讯录
- **P1-3: Relay 注册认证** — 新增 `RELAY_AUTH_SECRET` 环境变量，配置后注册需携带 HMAC-SHA256 签名令牌 + 5 分钟时间窗口校验，防止未授权连接冒充龙虾号
- **P1-4: 消息去重防重放** — `C2CHandler` 新增 `_seen_message_ids` 缓存，相同 `message_id` 在 5 分钟窗口内仅处理一次，配合签名时间窗口彻底封堵重放攻击
- **P1-5: 收件箱/对话加密存储** — `c2c_inbox` 和 `conversation_buffer` 表的 `content` 字段使用 Fernet 本地加密后存储，读取时自动解密（兼容旧明文数据），防止 SQLite 文件泄露导致消息明文暴露
- **P1-7: HKDF salt 语义明确化** — `derive_key()` 的 `salt` 参数默认值从 `b""` 改为 `None`，消除空字节与 None 的隐式转换歧义，文档补充 HKDF 规范说明

#### Changed
- 版本号 1.4.1 → 1.4.2

---

## [1.4.0] - 2026-03-25

### 🔐 E2E 端到端加密 — Relay 只看密文

龙虾间的悄悄话，连中继服务器也听不到。

#### Added — AES-256-GCM 端到端加密
- **`weclaw/claw2claw/crypto.py`** — 全新 E2E 加密模块
  - `CryptoEngine` 类：AES-256-GCM 对称加密 + HKDF-SHA256 密钥派生
  - `encrypt_message_fields()` — 加密 content（文本）和 payload（JSON）
  - `decrypt_message_fields()` — 解密并还原明文
  - 每条消息独立随机 nonce（12 字节），杜绝重放攻击
  - 密钥派生使用固定 salt + `weclaw-e2e-v1` info，同一 shared_secret 派生唯一 AES 密钥
- **`C2CMessage.encrypt()` / `.decrypt()`** — 消息级加解密方法（链式调用）
  - 新增字段：`encrypted: bool`、`e2e_nonce`、`e2e_payload_nonce`
  - 加密后 `content` → 密文（Base64），`payload` → `{"_e2e": "密文"}`
- **自动加密流程** — `client.py` 发送前：先 `encrypt()` 再 `sign()`（对密文签名）
- **自动解密流程** — `handler.py` 接收后：先 `verify()` 再 `decrypt()`（验签后解密）
- **解密失败信任事件** — 解密失败记录 `decrypt_fail` 信任事件（-10 分）

#### Changed
- `cryptography>=42.0.0` 加入核心依赖（`requirements.txt` + `pyproject.toml`）
- 版本号 1.2.0 → 1.4.0

#### Security
- **零知识中继** — Relay Server 只转发密文，无法读取消息内容
- **前向安全设计** — 每条消息使用独立 nonce，单条消息泄露不影响其他消息
- **防篡改** — 加密后签名（Encrypt-then-Sign），任何篡改都会被验签拦截
- **加密失败即拒发** — 加密失败时拒绝发送消息（不会静默降级为明文），确保零信息泄露
- **传输层 TLS 强制** — 默认 `wss://` 加密连接，Relay Server 支持 TLS 证书配置
- **密钥加密存储** — `shared_secret` 使用机器绑定密钥（Fernet）加密后存入 SQLite，防止本地文件泄露

---

## [1.3.0] - 2026-03-25

### 🦞 龙虾号加好友（双模式）

#### Added — 通过龙虾号发送好友申请
- **`pair_by_id` 协议消息** — 新增服务端 `_handle_pair_by_id()` 处理器，通过龙虾号直接查找在线用户发送好友申请
  - 直接在 `self._online` 中按龙虾号查找目标，不消耗加好友码
  - 完整校验：不能加自己、不能重复加好友、不能重复发送待处理申请
- **RelayClient `add_friend_by_id()`** — 客户端新增通过龙虾号加好友方法，与 `add_friend_by_code()` 共用 `_pair_result` Future 模式
- **终端双模式命令解析** — `龙虾加好友` 命令同时支持：
  - `龙虾加好友 claw_alice` — 通过龙虾号（主要方式）
  - `龙虾加好友 #1234` — 通过加好友码（面对面快捷方式）
- **SDK `add_friend()` 自动检测** — 传入 `claw_` 前缀自动走龙虾号路径，否则走加好友码路径
  ```python
  await claw.add_friend("claw_alice")  # 龙虾号（主要方式）
  await claw.add_friend("#1234")       # 加好友码（面对面快捷方式）
  ```

#### Changed
- 龙虾号从"仅用于身份标识"升级为**主要加好友方式**（类似微信号）
- 加好友码降级为"面对面快捷方式"（类似微信面对面加好友）
- 终端连接成功消息优先展示龙虾号加好友方式
- README / README.zh-CN / DAY1_GUIDE 文档同步更新

---

## [1.2.0] - 2026-03-24

### 🔐 好友安全升级 + 自定义龙虾号

#### Added — 好友申请二次确认机制
- **好友申请-确认流程** — 加好友不再"一步到位"，需对方确认后才正式成为好友
  - `friend_request` — 服务端→目标：有人想加你好友
  - `friend_request_sent` — 服务端→发起方：你的申请已发送
  - `friend_accept` / `friend_reject` — 目标→服务端：接受/拒绝申请
  - `friend_request_result` — 服务端→发起方：通知申请结果（接受/拒绝/过期）
  - `pending_requests` / `pending_requests_list` — 客户端↔服务端：查看待处理申请
- **待处理申请缓存** — `_pending_friend_requests` 本地缓存，支持离线后重新获取
- **24 小时自动过期** — `FRIEND_REQUEST_TTL = 86400`，过期申请自动清理
- 终端新命令：
  - `龙虾同意 <龙虾号>` — 接受好友申请
  - `龙虾拒绝 <龙虾号>` — 拒绝好友申请
  - `龙虾申请列表` / `龙虾申请` / `好友申请` — 查看待处理好友申请
- SDK 新 API：
  - `@claw.on_friend_request` — 收到好友申请回调（区分于 `on_friend_added`）
  - `await claw.accept_friend(request_id)` — 接受好友申请
  - `await claw.reject_friend(request_id)` — 拒绝好友申请
  - `await claw.pending_friend_requests()` — 获取待处理申请列表

#### Added — 自定义龙虾号（`claw_` 前缀）
- **统一 `claw_` 前缀** — 龙虾号格式从 `lobster_XXXXXXXX` 升级为 `claw_<自定义部分>`
- `validate_lobster_id()` — 龙虾号格式验证函数（3-20 字符，小写字母+数字+下划线，字母开头）
- `generate_lobster_id()` — 自动生成随机龙虾号（`claw_` + 8 位 hex）
- **首次启动交互式设定** — 终端模式首次启动时提示用户输入自定义龙虾号，回车跳过则自动生成
- **向后兼容** — Relay Server 同时接受 `claw_` 和 `lobster_` 前缀

#### Changed
- `add_friend("#1234")` 的消息从"加好友成功"改为"好友申请已发送，等待确认"
- `AgentCard.lobster_id` 默认前缀从 `lobster_` 改为 `claw_`
- SDK `_on_friend_added` 回调语义修正 — 拆分为 `on_friend_request`（收到申请）和 `on_friend_added`（成功添加）
- 修复 SDK `trust_score=50`（低于通信阈值 70）的 bug → 改为 `trusted=True`（=70）

---

## [1.1.0] - 2026-03-24

### 🏗️ 架构重构 — 从"社交智能代理"到"社交通信协议 SDK"

WeClaw 从"社交智能代理"向"社交通信协议 SDK"全面转型。
核心理念：WeClaw 是 AI Agent 的通信协议层（"龙虾的微信"），不是 AI Agent 本身。
WeClaw 负责身份、通讯录、消息传递、NAT 穿越和信任验证；AI 智能由外部实现。

### Added
- **`weclaw/sdk.py`** — 全新 SDK 公共 API 层（Phase 1）
  - `WeClaw` 类：统一入口，封装身份/通讯录/消息/加好友
  - `await claw.send(to, message)` — 向龙虾发消息
  - `claw.contacts()` — 获取通讯录
  - `await claw.add_friend("#1234")` — 通过加好友码加好友
  - `claw.my_card()` — 获取我的名片
  - `@claw.on_message` — 注册消息回调（装饰器模式）
  - `@claw.on_friend_request` — 注册好友请求回调
  - `async with WeClaw() as claw:` — 上下文管理器支持
  - 身份自动持久化（lobster_id 重启不变）
  - 零 Brain 依赖 — AI 逻辑完全由外部回调控制
- **`tests/test_sdk.py`** — SDK 基础测试
- `from weclaw import WeClaw` — 顶层导出

### Changed
- `__init__.py` 文案从"社交智能代理"改为"社交通信协议 SDK"
- 版本号 1.0.0 → 1.1.0

### Removed — AI 解耦（Phase 2-6）
- **Brain 类移除（Phase 2）** — `brain/core.py` 精简为弃用存根，仅保留数据模型（`MessageIntent`、`ReplyDigest` 等）和工具函数（`mask_api_key`）
- **Terminal 解耦（Phase 2）** — `terminal.py` 12+ 处 Brain 耦合点全部移除，AI 意图解析替换为正则命令匹配
- **ContactMemory 简化（Phase 3）** — 移除 `ai_summary` 字段、`generate_ai_summary()`、`update_ai_summary()` 方法
- **C2C Handler 解耦（Phase 5）** — `handler.py` 移除 `ai_digest_fn` 参数，查询消息不再自动 AI 回答，统一通知主人处理
- **依赖瘦身（Phase 6）** — `openai` 和 `tenacity` 从核心依赖移至可选 `[ai]` extras（`pip install weclaw[ai]`）

---

## [1.0.0] - 2026-03-24

### 🎉 首个正式发布版本

WeClaw 1.0.0 汇集了 v0.2 ~ v0.8 的所有功能，正式面向公众发布。

### Added
- **终端模式** — 3 分钟上手，AI 代写+确认发送+摘要回复+自动学习
- **🦞↔🦞 Claw-to-Claw 龙虾间通信** — Agent-to-Agent 协议，灵感来自 Google A2A
- **Relay 零配置互联** — WebSocket 中继，无需公网 IP，龙虾号+加好友码即可连接
- **持久龙虾号+好友系统** — 永久身份，加一次好友永久在线
- **渐进式信任系统** — 0-100 信任分，从陌生→引荐→握手→信任→完全信任
- **龙虾发现** — 通过标签搜索在线龙虾，拓展社交网络
- **好友引荐** — 让好友帮你介绍新朋友，信任链传递
- **AgentCard v0.8** — 支持 tags、handle、trust_score、profile、行为规则
- **龙虾画像 (Agent Profile)** — 结构化自我介绍（借鉴 Tobira.ai）
- **对话上下文** — 最近 N 轮对话注入 AI，支持指代消解
- **状态持久化** — SQLite 存储，重启不丢失
- **AI 降级保护** — AI 不可用时通知你，不发送垃圾消息
- **Web 管理界面** — 人脉通讯录、联系人画像、交互时间线
- **Docker 支持** — Relay Server + 主程序 Dockerfile + docker-compose

---

## [0.8.0] - 2026-03-20

### Added
- 龙虾画像 (Agent Profile) — 结构化自我介绍
- 龙虾行为规则 (Behavior Rules) — 安全与自动化配置
- AgentCard 新增 `profile` 和 `behavior` 字段

## [0.7.0] - 2026-03-15

### Added
- 渐进式信任系统 — 0-100 信任分
- 龙虾发现 (Agent Discovery) — 按标签搜索在线龙虾
- 好友引荐 (Friend Introduction) — 信任链传递
- AgentCard 新增 `tags`、`handle`、`trust_score`、`profile_url` 字段
- Relay Server 新增 `discover` 和 `introduce` 协议消息
- PeerInfo `trusted: bool` → `trust_score: int (0-100)`
- StateStore 新增 `trust_events` 表

## [0.6.0] - 2026-03-10

### Added
- 持久龙虾号 + 好友系统 (Relay v2 协议)
- 永久龙虾号 `lobster_XXXXXXXX`（首次启动自动生成，重启不变；v1.2 起升级为 `claw_` 前缀）
- 一次性加好友码 `#XXXX`（10 分钟有效）
- 好友列表 SQLite 持久化
- Relay Server v2（零存储，只转发）

### Changed
- 临时邀请码 → 永久龙虾号
- Session-based 配对 → 好友系统
- `龙虾连接` → `龙虾加好友`

## [0.5.0] - 2026-03-05

### Added
- 🦞↔🦞 Claw-to-Claw 龙虾间通信 (Relay 模式)
- WebSocket Relay Server
- Relay Client 自动重连

## [0.4.0] - 2026-03-01

### Added
- 🦞↔🦞 Claw-to-Claw 龙虾间通信 (HTTP 直连)
- Agent Card（龙虾名片）
- C2C Message（龙虾消息，HMAC-SHA256 签名）
- Peer Registry（龙虾通讯录）
- Handshake（握手协议）

## [0.3.0] - 2026-02-25

### Added
- Web 管理界面（人脉通讯录）
- 联系人搜索、排序、编辑、删除、合并
- 交互时间线
- Token 鉴权
- 响应式设计

## [0.2.0] - 2026-02-20

### Added
- 状态持久化（SQLite）
- 对话上下文（ConversationBuffer）
- AI 重试 + 降级保护
- 统一意图解析

## [0.1.0] - 2026-02-15

### Added
- 初始版本
- AI 代写消息
- 终端交互
- 人脉记忆（YAML）
