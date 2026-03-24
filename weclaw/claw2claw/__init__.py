"""
Claw-to-Claw (C2C) — 龙虾间通信协议

龙虾 A ↔ 龙虾 B 的直接通信层。
灵感来自 Google A2A 协议，但为个人 Agent 场景大幅简化。

核心概念：
- Agent Card（龙虾名片）: 告诉别的龙虾"我是谁、我能做什么、怎么联系我"
- C2C Message（龙虾消息）: 龙虾间传递的标准消息格式
- C2C Client（通信客户端）: 主动发消息给别的龙虾
- C2C Endpoints（通信端点）: 接收别的龙虾发来的消息
"""
