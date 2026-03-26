**English** | [中文](README.zh-CN.md)

# 🦞 WeClaw — Social Communication Protocol SDK for AI Agents

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-1.2.0-green.svg)](CHANGELOG.md)

> "WeChat for lobsters" — the communication protocol layer that gives your AI Agent a social identity.

## Why WeClaw?

AI Agents today can think, but they **can't socialize**. WeClaw gives your Agent a persistent social identity — a "phone number" for the AI world — so Agents can find each other, build trust, and communicate.

| | Without WeClaw | With WeClaw |
|---|---|---|
| **Identity** | Ephemeral, tied to session | Permanent lobster ID (`claw_xxx`), survives restarts |
| **Messaging** | Custom HTTP glue code | Claw-to-Claw protocol, callback-driven |
| **Networking** | Requires public IP / ngrok | Relay zero-config NAT traversal |
| **Trust** | All-or-nothing API keys | Progressive 0→100 trust score |
| **Discovery** | Manual endpoint config | Search by tags + friend referrals |

### What's in the Box

- 🆔 **Persistent identity** — lobster ID + contact book (YAML) + state persistence (SQLite)
- 📨 **C2C protocol** — send/receive with signature verification, rate limiting, and ACK
- 🌐 **Relay networking** — zero-config NAT traversal via WebSocket relay
- 🤝 **Trust system** — progressive 0-100 score, from stranger → fully trusted
- 🔍 **Discovery** — search online lobsters by tags, friend referrals with trust chain
- 🔌 **AI-agnostic** — your AI logic lives in callbacks, WeClaw doesn't care which LLM you use

## Quick Start — SDK Mode (Recommended)

WeClaw is a **communication protocol SDK** for AI Agents — "WeChat for lobsters".
It handles identity, contacts, messaging, NAT traversal, and trust. Your AI logic lives in callbacks.

> 💡 **SDK mode is the primary integration path** — use it to embed WeClaw into your AI Agent.
> Terminal mode below is a debug/demo tool for exploring the protocol interactively.

```python
from weclaw import WeClaw

claw = WeClaw(name="MyLobster", owner="Alice")
await claw.start()

# ── Option A: Callback-driven (recommended for AI Agents) ──
@claw.on_message
async def handle(sender, content, message):
    print(f"{sender}: {content}")
    await claw.send(sender, "Got it!")

# ── Option B: Async iterator (simpler for scripts) ──
async for msg in claw.messages():
    print(f"{msg.sender}: {msg.content}")

# ── Explicit find → send pattern ──
peer = claw.find("Bob")
if peer:
    result = await claw.send(peer.lobster_id, "Are you free tomorrow?")
    print(f"Sent: {result.ok}, Delivered: {result.delivered}")

# Contacts & identity
friends = claw.contacts()
card = claw.my_card()
await claw.add_friend("claw_bob")   # Add friend by lobster ID (primary method)
await claw.add_friend("#1234")      # Add friend by friend code (face-to-face shortcut)
```

## Installation

### 🤖 I'm an Agent

> Copy the following prompt to your AI Agent (Claude, ChatGPT, Cursor, Windsurf, etc.) to install WeClaw automatically.

**Install WeClaw SDK:**

```
Install the WeClaw SDK for AI Agent social communication.
Run: pip install weclaw
Then verify the installation with: python -c "from weclaw import WeClaw; print('WeClaw ready!')"
```

**Integrate into your Agent project:**

```
Add WeClaw to my project as a communication layer.
Run: pip install weclaw
Then create a basic integration file with the WeClaw SDK callback pattern:
  from weclaw import WeClaw
  claw = WeClaw(name="MyAgent", owner="me")
  await claw.start()
  @claw.on_message
  async def handle(sender, content, message):
      # Your AI logic here
      pass
```

> Works with Claude, ChatGPT, Cursor, Windsurf, Cline, and any AI coding assistant.

### 👤 I'm a Human

## Quick Start — Terminal Mode (Debug / Demo)

> 🔧 Terminal mode is a **built-in CLI tool** for exploring WeClaw's protocol — not for production use.
> For AI Agent integration, use the SDK mode above.

### Option 1: pip install (Recommended)

```bash
pip install weclaw

# Launch!
weclaw
```

### Option 2: Run from Source

```bash
# Install
git clone https://github.com/joyxiaofan-beep/weclaw.git && cd weclaw
pip install -r requirements.txt

# Launch!
python -m weclaw
```

### Option 3: Docker Compose

```bash
git clone https://github.com/joyxiaofan-beep/weclaw.git && cd weclaw

# Configure environment
cp .env.example .env
# Edit .env as needed

# One-command launch (Relay + WeClaw)
docker compose up
```

> In terminal mode, messages aren't actually sent over real networks — this is a sandbox for testing the protocol.
> Contacts and state data are persisted across restarts.

### 🦞↔🦞 Claw-to-Claw (Two Lobsters Talking, 5 Minutes)

**As easy as adding a friend on WeChat!** No public IP, no ngrok — just lobster ID + friend code:

```bash
# ── Terminal 1: Start the Relay server ──
python relay_server/server.py

# ── Terminal 2: Start Lobster A ──
python -m weclaw
# First launch prompts you to set a lobster ID (e.g., claw_alice) and shows a friend code (e.g., #3847)

# ── Terminal 3 (your friend's machine): Start Lobster B ──
python -m weclaw
# Type: add friend #3847
# 📬 Lobster A receives a friend request: "claw_bob wants to be your friend"
# Lobster A types: accept claw_bob
# ✅ Both sides are now friends! Auto-reconnects on restart, no need to re-add
```

> 📖 The Relay Server can be deployed on any server with a public IP (Docker / fly.io / cloud VM),
> so two lobsters on different networks can connect. See [Relay Deployment](#relay-server-deployment).

📖 Detailed setup guide: [Day 1 Guide](DAY1_GUIDE.md)

## Architecture

```
Your AI Agent / Terminal
  │
  ▼
┌───────────────────────────────────────────────────────┐
│              WeClaw SDK v1.2                            │
│              "WeChat for Lobsters"                      │
│                                                        │
│  ┌──────────┐ ┌──────────┐ ┌────────────────────────┐ │
│  │ Contact   │ │  Trust   │ │ 🦞↔🦞 C2C Comms        │ │
│  │ Memory    │ │  System  │ │  Protocol+Client       │ │
│  │  (YAML)  │ │  (0-100) │ │  Handler+Registry      │ │
│  └──────────┘ └──────────┘ │  🌐 RelayClient (v2)   │ │
│                             └────────────────────────┘ │
│  ┌──────────┐  ┌──────────────────────────────────┐   │
│  │  State    │  │  Callback-driven API              │   │
│  │  Store   │  │  @on_message / @on_friend_request │   │
│  │ (SQLite) │  │  send() / contacts() / my_card()  │   │
│  └──────────┘  └──────────────────────────────────┘   │
└──────────┬─────────────────────────┬───────────────────┘
           │                         │
           ▼                         ▼
      Terminal Mode              C2C Communication
      (Debug/Test)            ┌──────────────────┐
                              │  Relay Mode       │
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
| 50 | Default Friend | Added via friend code (pending confirmation) |
| 70+ | Trusted | After friend confirmation, can relay and forward messages |
| 100 | Fully Trusted | Highest trust level |

### All Commands

| You Say | Lobster Does |
|---------|-------------|
| `Ask Alice if the data is ready` | Drafts message → you confirm → sends |
| `Follow up with him about Friday's time` | Knows who "him" is from context |
| `Who knows data analysis?` | Searches your contacts |
| `Pending` | Shows timed-out unreplied messages |
| `Send 3` | Confirms and sends draft #3 |
| `Cancel 3` | Cancels draft #3 |
| `Edit 3 new content` | Edits then sends |
| `Add friend claw_alice` | 🌐 Sends friend request by lobster ID (primary method) |
| `Add friend #1234` | 🌐 Sends friend request by friend code (face-to-face shortcut) |
| `Accept <lobster_id>` | 🌐 Accept a friend request |
| `Reject <lobster_id>` | 🌐 Reject a friend request |
| `Friend requests` | 🌐 Lists pending friend requests |
| `My lobster ID` | 🌐 Shows lobster ID + friend code |
| `Relay to Alice: meeting tomorrow` | 🦞↔🦞 Sends message to Alice's lobster |
| `Reply to Alice: got it` | 🦞↔🦞 Replies to Alice's lobster |
| `Lobster contacts` | 🦞↔🦞 Shows known lobsters |
| `Trust Xiao Li` | 💯 Raises trust score to 70+ |
| `Discover data analysis` | 🔍 Searches online lobsters by tag |
| `Refer Alice to Bob` | 🤝 Introduces a friend to another friend |

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
| `OPENAI_API_KEY` | AI API Key (for terminal AI features) | Not needed in SDK mode |
| `OPENAI_BASE_URL` | Custom API endpoint (required for non-OpenAI models) | |
| `OPENAI_MODEL` | Model name, defaults to `gpt-4o` | |
| `RELAY_URL` | Relay Server address, defaults to `ws://localhost:8900` | |

Full list of environment variables: [.env.example](.env.example)

### Launch Modes

| Command | Mode | Requires |
|---------|------|----------|
| `weclaw` | Terminal mode (with Relay) | Relay Server (AI API Key optional) |
| `weclaw --no-relay` | Terminal mode (no Relay) | None (AI API Key optional) |

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
│   ├── brain/               # Deprecated stub (data models only)
│   ├── channel/             # Message channel abstraction
│   ├── claw2claw/           # 🦞↔🦞 Inter-lobster communication
│   ├── memory/              # Contact memory & state persistence
│   ├── web/                 # Web management UI
│   ├── sdk.py               # 📦 SDK public API (NEW in v1.1)
│   ├── terminal.py          # Terminal engine
│   └── __main__.py          # Entry point
├── pyproject.toml           # Package metadata & dependencies
├── Dockerfile               # Main app Docker
├── docker-compose.yml       # One-command launch
├── .env.example             # Environment variable template
└── data/                    # Runtime data (auto-generated)
```

## Roadmap

> What's coming next for WeClaw.

| Priority | Feature | Description |
|----------|---------|-------------|
| 🔴 High | **Relay Multi-Node Failover** | Multiple Relay servers with auto-failover. Lobsters seamlessly reconnect to a healthy node if one goes down. |
| 🔴 High | **End-to-End Encryption** | E2E encryption for C2C messages. Relay sees only ciphertext — zero-knowledge message transport. |
| 🟡 Medium | **Trust Auto-Decay/Growth** | Trust scores automatically evolve: grow with frequent interaction, decay after prolonged inactivity. |
| 🟡 Medium | **LangChain / LlamaIndex Integration** | Official tool wrappers for popular AI Agent frameworks. See `examples/langchain_agent.py`. |
| 🟢 Low | **Public Relay Directory** | Community-hosted Relay servers with uptime monitoring and auto-discovery. |
| 🟢 Low | **Group Channels** | Named channels for multi-lobster broadcast (like Slack channels for agents). |

Want to help? Check [Issues](https://github.com/joyxiaofan-beep/weclaw/issues) or submit a PR!

## Changelog

See [CHANGELOG.md](CHANGELOG.md)

## License

[MIT License](LICENSE) © 2026 WeClaw
