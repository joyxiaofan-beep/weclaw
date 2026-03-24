# 🦞 WeClaw — 龙虾社交智能代理

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.0.0-green.svg)](CHANGELOG.md)

> 让你的龙虾成为你与人际网络之间的智能接口。

## 核心理念

WeClaw 不是一个聊天机器人，它是你的**社交代理**：
- 📨 代你向同事发送消息（终端预览 / 龙虾互联）
- 👂 接收回复并提炼关键信息
- 🧠 从每次交互中学习，逐步建立人脉画像
- 🎯 知道该找谁、怎么问、怎么整合答案
- 💬 跟龙虾对话就是给它下指令（终端 / 龙虾互联均可）
- ⏰ 自动追踪待回复消息，超时提醒
- 🧵 **对话上下文**——龙虾记得"刚才说的话"，支持"他""那件事"等指代
- 📦 **状态持久化**——重启不丢失任何待处理消息和追踪记录
- 🛡️ **AI 降级保护**——AI 不可用时通知你，不发送垃圾消息
- 🖥️ **终端模式**——3 分钟上手，零配置即可体验全部核心功能
- 🦞↔🦞 **Claw-to-Claw**——你的龙虾可以直接跟朋友的龙虾对话！
- 🌐 **Relay 零配置互联**——无需公网 IP，龙虾号+加好友码即可连接
- 🔍 **龙虾发现**——通过标签搜索在线龙虾，拓展社交网络
- 🤝 **好友引荐**——让好友帮你介绍新朋友，信任链传递
- 💯 **渐进式信任**——从陌生→引荐→握手→信任→完全信任，0-100 信任分

## 快速开始

### 方式一：pip install（推荐）

```bash
pip install weclaw

# 设置 API Key
export OPENAI_API_KEY=sk-xxxxxxxx

# 启动！
weclaw
```

### 方式二：从源码运行

```bash
# 安装
git clone https://github.com/joyxiaofan-beep/weclaw.git && cd weclaw
pip install -r requirements.txt

# 设置 API Key（任选一种方式）
export OPENAI_API_KEY=sk-xxxxxxxx        # 方式一：环境变量
# 或 cp config/config.terminal.yaml config/config.yaml  # 方式二：配置文件

# 启动！
python -m weclaw
```

### 方式三：Docker Compose

```bash
git clone https://github.com/joyxiaofan-beep/weclaw.git && cd weclaw

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY

# 一键启动（Relay + WeClaw）
docker compose up
```

> 终端模式下消息不会真正发出，但 AI 代写、确认发送、摘要回复、自动学习等核心能力完全可用。
> 联系人和对话数据持久化保存。

### 🦞↔🦞 龙虾互联（两只龙虾对话，5 分钟）

**像微信加好友一样简单！** 无需公网 IP，无需 ngrok，龙虾号+加好友码即可连接：

```bash
# ── 终端 1：启动 Relay 中继服务器 ──
python relay_server/server.py

# ── 终端 2：启动龙虾 A ──
python -m weclaw
# 首次启动自动生成龙虾号（如: lobster_a3f7b2c1），并显示加好友码（如: #3847）

# ── 终端 3（你朋友的电脑）：启动龙虾 B ──
python -m weclaw
# 输入: 龙虾加好友 #3847
# ✅ 好友添加成功！以后重启自动互认，不需要再加好友
```

> 📖 Relay Server 可以部署在任意有公网 IP 的服务器上（Docker / fly.io / 云服务器），
> 这样两只龙虾在不同网络也能连接。详见 [Relay 部署](#relay-server-部署)。

📖 详细配置步骤见 [Day 1 操作指引](DAY1_GUIDE.md)

## 架构

```
你（终端）
  │
  ▼
┌───────────────────────────────────────────────────────┐
│              WeClaw 核心大脑 v1.0                       │
│                                                        │
│  ┌──────────┐ ┌──────────┐ ┌────────────────────────┐ │
│  │ 人脉记忆  │ │ AI 对话层 │ │ 🦞↔🦞 C2C 通信         │ │
│  │  (YAML)  │ │ (OpenAI) │ │  Protocol+Client       │ │
│  └──────────┘ └──────────┘ │  Handler+Registry      │ │
│                             │  🌐 RelayClient (v2)   │ │
│                             └────────────────────────┘ │
│  ┌──────────┐ ┌──────────────────────┐                 │
│  │ 超时追踪  │ │ 对话上下文 (SQLite)    │                 │
│  │ (SQLite) │ │ 最近 N 轮对话 → AI    │                 │
│  └──────────┘ └──────────────────────┘                 │
│  ┌──────────────────────────────────┐                  │
│  │    统一 AI 意图解析 + 快速路径     │                  │
│  └──────────────────────────────────┘                  │
└──────────┬─────────────────────────┬───────────────────┘
           │                         │
           ▼                         ▼
      终端通道                    C2C 通信层
      (CLI)                  ┌──────────────────┐
                   │      ▲      │  Relay 模式 (默认) │
                同事A  同事B     │  ↕ WebSocket       │
                                 │  ↕ 龙虾号+加好友码  │
                                 │                    │
                                 │  HTTP 模式 (高级)   │
                                 │  ↕ 直连 POST       │
                                 └────────┬───────────┘
                                          │
                            ┌──────────────┴──────────────┐
                            ▼                              ▼
                    🌐 Relay Server v2            🦞 远程龙虾 (HTTP)
                    (WebSocket 中继)
                    ┌──────────────┐
                    │ 龙虾号 lobster_xx │
                    │ 加好友码 #XXXX    │
                    │ 好友路由(不存储)  │
                    │ 心跳+自动清理    │
                    └───────┬──────┘
                            │
                    🦞 龙虾A ↔ 🦞 龙虾B
```

## 功能一览

### 渐进式信任系统

| 分数范围 | 等级 | 含义 |
|---------|------|------|
| 0 | 陌生人 | 未建立任何关系 |
| 5 | 被引荐 | 通过好友引荐认识 |
| 10 | 已握手 | 完成龙虾握手 |
| 50 | 默认好友 | 通过加好友码添加 |
| 70+ | 已信任 | 可以传话、转发消息 |
| 100 | 完全信任 | 最高信任等级 |

### 所有命令

| 你说 | 龙虾做 |
|------|--------|
| `帮我问小王数据好了没` | 生成消息草稿 → 你确认 → 发送 |
| `帮我跟他确认一下周五几点` | 根据上下文知道"他"是谁 |
| `谁懂数据分析` | 查找人脉 |
| `待办` | 查看超时未回复的消息 |
| `发送3` | 确认发送草稿 #3 |
| `取消3` | 取消草稿 #3 |
| `改3 新的内容` | 修改后发送 |
| `龙虾加好友 #1234` | 🌐 通过加好友码添加好友 |
| `龙虾号` / `我的龙虾号` | 🌐 查看龙虾号 + 加好友码 |
| `龙虾传话 老王 明天开会` | 🦞↔🦞 给老王的龙虾传话 |
| `龙虾回 老王 好的收到` | 🦞↔🦞 回复老王龙虾的消息 |
| `龙虾通讯录` | 🦞↔🦞 查看已认识的龙虾 |
| `龙虾信任 小李` | 💯 将信任分提升到 70+ |
| `龙虾发现 数据分析` | 🔍 按标签搜索在线龙虾 |
| `龙虾引荐 老王 小李` | 🤝 把好友介绍给另一个好友 |

## 配置说明

### 终端模式（最小配置）

```yaml
# config/config.yaml（或用环境变量代替）
ai:
  api_key: "sk-xxxxxxxx"           # 必填
  model: "gpt-4o"                  # 可选，默认 gpt-4o
  # base_url: "https://api.deepseek.com/v1"  # 用第三方模型时取消注释

behavior:
  confirm_before_send: true        # 发送前确认
  tone: "auto"                     # 语气自动匹配
```

### 环境变量

所有配置都可以用环境变量代替（优先级高于配置文件）：

| 变量 | 说明 | 必填 |
|------|------|------|
| `OPENAI_API_KEY` | AI API Key | ✅ |
| `OPENAI_BASE_URL` | 自定义 API 地址（国产模型必填） | |
| `OPENAI_MODEL` | 模型名称，默认 `gpt-4o` | |
| `RELAY_URL` | Relay Server 地址，默认 `ws://localhost:8900` | |

完整环境变量列表见 [.env.example](.env.example)

### 启动方式

| 命令 | 模式 | 需要 |
|------|------|------|
| `weclaw` | 终端模式（含 Relay） | AI API Key + Relay Server |
| `weclaw --no-relay` | 终端模式（无 Relay） | AI API Key |

## Relay Server 部署

**本地开发：**

```bash
python relay_server/server.py
# 监听 ws://0.0.0.0:8900
```

**Docker：**

```bash
cd relay_server
docker build -t weclaw-relay .
docker run -p 8900:8900 weclaw-relay
```

**Docker Compose（推荐）：**

```bash
# Relay + WeClaw 一键启动
docker compose up
```

**Relay 环境变量：**

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RELAY_HOST` | `0.0.0.0` | 监听地址 |
| `RELAY_PORT` | `8900` | 监听端口 |
| `RELAY_MAX_LOBSTERS` | `2000` | 最大同时在线龙虾数 |

## 安全设计

| 端点 | 鉴权方式 | 说明 |
|------|---------|------|
| `POST /send` | Bearer Token | 发送消息 |
| `POST /c2c/incoming` | HMAC-SHA256 签名 | 龙虾间消息 |
| `GET /c2c/card` | 无 | 龙虾公开名片 |
| `GET /health` | 无 | 健康检查 |

- 📊 所有日志均做脱敏处理
- 🔐 API 响应中的用户 ID 做掩码处理

## 目录结构

```
weclaw/
├── config/                  # 配置文件
│   ├── config.example.yaml  # 完整配置模板
│   └── config.terminal.yaml # 终端模式最小配置
├── relay_server/            # 🌐 Relay 中继服务器（独立部署）
│   ├── server.py            # WebSocket 中继服务器 v2
│   └── Dockerfile           # Docker 部署
├── weclaw/                  # 核心代码
│   ├── brain/               # AI 核心
│   ├── channel/             # 消息通道抽象层
│   ├── claw2claw/           # 🦞↔🦞 龙虾间通信
│   ├── memory/              # 记忆系统
│   ├── web/                 # Web 管理界面
│   ├── terminal.py          # 终端引擎
│   └── __main__.py          # 启动入口
├── pyproject.toml           # 包元数据 & 依赖
├── Dockerfile               # 主程序 Docker
├── docker-compose.yml       # 一键启动
├── .env.example             # 环境变量模板
└── data/                    # 运行时数据（自动生成）
```

## 更新日志

详见 [CHANGELOG.md](CHANGELOG.md)

## 许可证

[MIT License](LICENSE) © 2026 WeClaw
