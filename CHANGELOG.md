# Changelog

All notable changes to WeClaw will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- 永久龙虾号 `lobster_XXXXXXXX`（首次启动自动生成，重启不变）
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
