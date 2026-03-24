**English** | [中文](README.zh-CN.md)

# 🦞 WeClaw — Your Social Intelligence Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.0.0-green.svg)](CHANGELOG.md)

> Turn your lobster into the smart interface between you and your social network.

## Core Philosophy

WeClaw is not a chatbot — it's your **social agent**:
- 📨 Sends messages to colleagues on your behalf (terminal preview / Claw-to-Claw)
- 👂 Receives replies and distills key information
- 🧠 Learns from every interaction, gradually building contact profiles
- 🎯 Knows who to ask, how to ask, and how to synthesize answers
- 💬 Talking to your lobster = giving it instructions (terminal or C2C)
- ⏰ Auto-tracks pending replies with timeout reminders
- 🧵 **Conversation context** — your lobster remembers "what we just talked about", supports pronouns like "him" or "that thing"
- 📦 **State persistence** — restarts don't lose any pending messages or tracking records
- 🛡️ **AI fallback protection** — notifies you when AI is unavailable instead of sending garbage
- 🖥️ **Terminal mode** — get started in 3 minutes, zero config needed for full core experience
- 🦞↔🦞 **Claw-to-Claw** — your lobster can talk directly to your friend's lobster!
- 🌐 **Relay zero-config networking** — no public IP needed, connect with lobster ID + friend code
- 🔍 **Lobster discovery** — search online lobsters by tags to expand your network
- 🤝 **Friend referrals** — ask friends to introduce new contacts, trust chain propagation
- 💯 **Progressive trust** — from stranger → referred → handshake → trusted → fully trusted, 0-100 trust score

## Quick Start

### Option 1: pip install (Recommended)

```bash
pip install weclaw

# Set your API key
export OPENAI_API_KEY=sk-xxxxxxxx

# Launch!
weclaw
```

### Option 2: Run from Source

```bash
# Install
git clone https://github.com/joyxiaofan-beep/weclaw.git && cd weclaw
pip install -r requirements.txt

# Set API key (choose one)
export OPENAI_API_KEY=sk-xxxxxxxx        # Option A: environment variable
# or: cp config/config.terminal.yaml config/config.yaml  # Option B: config file

# Launch!
python -m weclaw
```

### Option 3: Docker Compose

```bash
git clone https://github.com/joyxiaofan-beep/weclaw.git && cd weclaw

# Configure environment
cp .env.example .env
# Edit .env and fill in OPENAI_API_KEY

# One-command launch (Relay + WeClaw)
docker compose up
```

> In terminal mode, messages aren't actually sent out, but AI drafting, send confirmation, reply summarization, and auto-learning are fully functional.
> Contacts and conversation data are persisted across restarts.

### 🦞↔🦞 Claw-to-Claw (Two Lobsters Talking, 5 Minutes)

**As easy as adding a friend on WeChat!** No public IP, no ngrok — just lobster ID + friend code:

```bash
# ── Terminal 1: Start the Relay server ──
python relay_server/server.py

# ── Terminal 2: Start Lobster A ──
python -m weclaw
# Auto-generates a lobster ID on first launch (e.g., lobster_a3f7b2c1) and shows a friend code (e.g., #3847)

# ── Terminal 3 (your friend's machine): Start Lobster B ──
python -m weclaw
# Type: add friend #3847
# ✅ Friend added! Auto-reconnects on restart, no need to re-add
```

> 📖 The Relay Server can be deployed on any server with a public IP (Docker / fly.io / cloud VM),
> so two lobsters on different networks can connect. See [Relay Deployment](#relay-server-deployment).

📖 Detailed setup guide: [Day 1 Guide](DAY1_GUIDE.md)

## Architecture

```
You (Terminal)
  │
  ▼
┌───────────────────────────────────────────────────────┐
│              WeClaw Core Brain v1.0                     │
│                                                        │
│  ┌──────────┐ ┌──────────┐ ┌────────────────────────┐ │
│  │ Contact   │ │ AI Chat  │ │ 🦞↔🦞 C2C Comms        │ │
│  │ Memory    │ │  Layer   │ │  Protocol+Client       │ │
│  │  (YAML)  │ │ (OpenAI) │ │  Handler+Registry      │ │
│  └──────────┘ └──────────┘ │  🌐 RelayClient (v2)   │ │
│                             └────────────────────────┘ │
│  ┌──────────┐ ┌──────────────────────┐                 │
│  │ Timeout   │ │ Conversation Context  │                │
│  │ Tracker  │ │ (SQLite)              │                 │
│  │ (SQLite) │ │ Last N turns → AI     │                 │
│  └──────────┘ └──────────────────────┘                 │
│  ┌──────────────────────────────────┐                  │
│  │  Unified AI Intent Parser +      │                  │
│  │  Fast Path Router                │                  │
│  └──────────────────────────────────┘                  │
└──────────┬─────────────────────────┬───────────────────┘
           │                         │
           ▼                         ▼
      Terminal                  C2C Communication
      Channel                ┌──────────────────┐
      (CLI)                  │  Relay Mode       │
                             │  (default)        │
                             │  ↕ WebSocket      │
                             │  ↕ LobsterID +    │
                             │    Friend Code    │
                             │                   │
                             │  HTTP Mode        │
                             │  (advanced)       │
                             │  ↕ Direct POST    │
                             └────────┬──────────┘
                                      │
                        ┌─────────────┴─────────────┐
                        ▼                            ▼
                🌐 Relay Server v2          🦞 Remote Lobster
                (WebSocket Relay)              (HTTP)
                ┌──────────────┐
                │ LobsterID     │
                │ Friend Code   │
                │ Route (no     │
                │   storage)    │
                │ Heartbeat +   │
                │  Auto-cleanup │
                └───────┬──────┘
                        │
                🦞 Lobster A ↔ 🦞 Lobster B
```

## Features

### Progressive Trust System

| Score | Level | Meaning |
|-------|-------|---------|
| 0 | Stranger | No relationship established |
| 5 | Referred | Met through friend referral |
| 10 | Handshake | Completed lobster handshake |
| 50 | Default Friend | Added via friend code |
| 70+ | Trusted | Can relay and forward messages |
| 100 | Fully Trusted | Highest trust level |

### All Commands

| You Say | Lobster Does |
|---------|-------------|
| `Ask Xiao Wang if the data is ready` | Drafts message → you confirm → sends |
| `Follow up with him about Friday's time` | Knows who "him" is from context |
| `Who knows data analysis?` | Searches your contacts |
| `Pending` | Shows timed-out unreplied messages |
| `Send 3` | Confirms and sends draft #3 |
| `Cancel 3` | Cancels draft #3 |
| `Edit 3 new content` | Edits then sends |
| `Add friend #1234` | 🌐 Adds friend via friend code |
| `My lobster ID` | 🌐 Shows lobster ID + friend code |
| `Relay to Lao Wang: meeting tomorrow` | 🦞↔🦞 Sends message to Lao Wang's lobster |
| `Reply to Lao Wang: got it` | 🦞↔🦞 Replies to Lao Wang's lobster |
| `Lobster contacts` | 🦞↔🦞 Shows known lobsters |
| `Trust Xiao Li` | 💯 Raises trust score to 70+ |
| `Discover data analysis` | 🔍 Searches online lobsters by tag |
| `Refer Lao Wang to Xiao Li` | 🤝 Introduces a friend to another friend |

## Configuration

### Terminal Mode (Minimal Config)

```yaml
# config/config.yaml (or use environment variables instead)
ai:
  api_key: "sk-xxxxxxxx"           # Required
  model: "gpt-4o"                  # Optional, defaults to gpt-4o
  # base_url: "https://api.deepseek.com/v1"  # Uncomment for third-party models

behavior:
  confirm_before_send: true        # Confirm before sending
  tone: "auto"                     # Auto-match conversation tone
```

### Environment Variables

All settings can be configured via environment variables (takes precedence over config file):

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENAI_API_KEY` | AI API Key | ✅ |
| `OPENAI_BASE_URL` | Custom API endpoint (required for non-OpenAI models) | |
| `OPENAI_MODEL` | Model name, defaults to `gpt-4o` | |
| `RELAY_URL` | Relay Server address, defaults to `ws://localhost:8900` | |

Full list of environment variables: [.env.example](.env.example)

### Launch Modes

| Command | Mode | Requires |
|---------|------|----------|
| `weclaw` | Terminal mode (with Relay) | AI API Key + Relay Server |
| `weclaw --no-relay` | Terminal mode (no Relay) | AI API Key |

## Relay Server Deployment

**Local development:**

```bash
python relay_server/server.py
# Listens on ws://0.0.0.0:8900
```

**Docker:**

```bash
cd relay_server
docker build -t weclaw-relay .
docker run -p 8900:8900 weclaw-relay
```

**Docker Compose (Recommended):**

```bash
# One-command launch: Relay + WeClaw
docker compose up
```

**Relay Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `RELAY_HOST` | `0.0.0.0` | Listen address |
| `RELAY_PORT` | `8900` | Listen port |
| `RELAY_MAX_LOBSTERS` | `2000` | Max concurrent online lobsters |

## Security Design

| Endpoint | Auth | Description |
|----------|------|-------------|
| `POST /send` | Bearer Token | Send message |
| `POST /c2c/incoming` | HMAC-SHA256 Signature | Inter-lobster messages |
| `GET /c2c/card` | None | Public lobster card |
| `GET /health` | None | Health check |

- 📊 All logs are sanitized (PII redacted)
- 🔐 User IDs in API responses are masked

## Project Structure

```
weclaw/
├── config/                  # Configuration files
│   ├── config.example.yaml  # Full config template
│   └── config.terminal.yaml # Minimal terminal config
├── relay_server/            # 🌐 Relay server (independently deployable)
│   ├── server.py            # WebSocket relay server v2
│   └── Dockerfile           # Docker deployment
├── weclaw/                  # Core source code
│   ├── brain/               # AI core
│   ├── channel/             # Message channel abstraction
│   ├── claw2claw/           # 🦞↔🦞 Inter-lobster communication
│   ├── memory/              # Memory system
│   ├── web/                 # Web management UI
│   ├── terminal.py          # Terminal engine
│   └── __main__.py          # Entry point
├── pyproject.toml           # Package metadata & dependencies
├── Dockerfile               # Main app Docker
├── docker-compose.yml       # One-command launch
├── .env.example             # Environment variable template
└── data/                    # Runtime data (auto-generated)
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md)

## License

[MIT License](LICENSE) © 2026 WeClaw
