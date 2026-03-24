"""
消息通道抽象层

支持多种消息收发通道：
- TerminalChannel: 纯终端交互（零配置，开箱即用）

通道只负责"收消息"和"发消息"，不关心 AI、记忆等业务逻辑。
"""

from weclaw.channel.base import BaseChannel, ChannelMessage
from weclaw.channel.terminal import TerminalChannel

__all__ = ["BaseChannel", "ChannelMessage", "TerminalChannel"]
