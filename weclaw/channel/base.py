"""
消息通道基类

定义所有通道必须实现的接口：
- send: 发消息给某人
- send_to_owner: 发消息给主人
- receive: 接收一条消息（异步）
"""

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field


class ChannelMessage(BaseModel):
    """通道收到的消息"""
    sender_id: str           # 发送者标识（用户名 / 外部ID / ...）
    sender_name: str = ""    # 发送者昵称
    content: str             # 消息内容
    is_from_owner: bool = False  # 是否来自主人
    raw: dict = Field(default_factory=dict)  # 原始数据（通道特有的额外信息）


class BaseChannel(ABC):
    """消息通道基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """通道名称"""
        ...

    @abstractmethod
    async def send(self, target_id: str, content: str) -> bool:
        """
        发消息给指定用户

        Args:
            target_id: 目标用户标识
            content: 消息内容

        Returns:
            是否发送成功
        """
        ...

    @abstractmethod
    async def send_to_owner(self, content: str) -> bool:
        """
        发消息给主人

        Args:
            content: 消息内容

        Returns:
            是否发送成功
        """
        ...

    @abstractmethod
    async def receive(self) -> Optional[ChannelMessage]:
        """
        接收一条消息（阻塞等待）

        Returns:
            收到的消息，或 None（通道关闭时）
        """
        ...

    async def start(self):
        """启动通道（可选覆盖）"""
        pass

    async def stop(self):
        """停止通道（可选覆盖）"""
        pass
