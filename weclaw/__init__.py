"""
WeClaw — 龙虾社交通信协议 SDK

AI Agent 的社交通信层 — "龙虾的微信"。
负责身份、通讯录、消息收发、NAT 穿透和信任验证。
不负责 AI 智能、记忆、人格或决策。

快速开始::

    from weclaw import WeClaw

    claw = WeClaw(name="小龙", owner="Alice")
    await claw.start()

    @claw.on_message
    async def handle(sender, content, message):
        await claw.send(sender, "收到！")

    # 或者使用异步迭代器
    async for msg in claw.messages():
        print(f"{msg.sender}: {msg.content}")

    # 显式 find → send 模式
    peer = claw.find("Bob")
    if peer:
        result = await claw.send(peer.lobster_id, "Hi!")
        print(result.ok, result.delivered)
"""

__version__ = "1.2.0"

from weclaw.sdk import WeClaw, SendResult, IncomingMessage

__all__ = ["WeClaw", "SendResult", "IncomingMessage", "__version__"]
