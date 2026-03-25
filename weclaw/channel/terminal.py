"""
终端交互通道 — TerminalChannel

纯命令行交互，不需要任何外部服务：
- 输入：从 stdin 读取
- 输出：彩色打印到 stdout
- 模拟"消息收发"：所有"发送"都打印到终端

非常适合：
1. 首次体验龙虾的核心能力
2. 开发调试

终端中的"联系人"全部是虚拟的——龙虾会生成消息但不会真正发送，
而是把草稿展示给你看。
"""

import asyncio
import sys
from datetime import datetime
from typing import Optional

from loguru import logger

from weclaw.channel.base import BaseChannel, ChannelMessage


# ──────────────────────────────────────────
# ANSI 颜色
# ──────────────────────────────────────────

class _C:
    """终端颜色常量"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_BLUE = "\033[44m"
    BG_GREEN = "\033[42m"

    # 组合
    LOBSTER = "\033[1;31m"     # 龙虾 = 红色加粗
    OWNER = "\033[1;36m"       # 主人 = 青色加粗
    SYSTEM = "\033[2;33m"      # 系统 = 暗黄
    SUCCESS = "\033[1;32m"     # 成功 = 绿色加粗
    WARNING = "\033[1;33m"     # 警告 = 黄色加粗
    INFO = "\033[34m"          # 信息 = 蓝色


def _print_lobster(msg: str):
    """龙虾说话"""
    lines = msg.split("\n")
    print(f"\n{_C.LOBSTER}  🦞 龙虾{_C.RESET}")
    for line in lines:
        print(f"  {_C.WHITE}{line}{_C.RESET}")
    print()


def _print_system(msg: str):
    """系统提示"""
    print(f"{_C.SYSTEM}  ⚙️  {msg}{_C.RESET}")


def _print_send_preview(target: str, content: str, explanation: str = ""):
    """展示"发送预览"——终端模式下消息不会真正发出"""
    print(f"\n{_C.SUCCESS}  📤 发送预览 → {target}{_C.RESET}")
    print(f"  {_C.BOLD}┌──────────────────────────────┐{_C.RESET}")
    for line in content.split("\n"):
        print(f"  {_C.BOLD}│{_C.RESET} {line}")
    print(f"  {_C.BOLD}└──────────────────────────────┘{_C.RESET}")
    if explanation:
        print(f"  {_C.DIM}💡 {explanation}{_C.RESET}")
    print()


def _print_divider():
    """分隔线"""
    print(f"{_C.DIM}{'─' * 50}{_C.RESET}")


# ──────────────────────────────────────────
# TerminalChannel
# ──────────────────────────────────────────

class TerminalChannel(BaseChannel):
    """
    纯终端交互通道

    特性：
    - send() → 彩色打印"发送预览"
    - send_to_owner() → 龙虾说话样式打印
    - receive() → 从 stdin 异步读取
    - 支持模拟回复（输入 `@小王 内容` 模拟小王回复了消息）
    """

    def __init__(self, owner_name: str = "主人"):
        self.owner_name = owner_name
        self._running = False

    @property
    def name(self) -> str:
        return "terminal"

    async def send(self, target_id: str, content: str) -> bool:
        """
        "发送"消息 — 在终端模式下打印发送预览

        终端模式不会真正发消息，而是展示龙虾生成的内容给用户看。
        """
        _print_send_preview(target_id, content)
        return True

    async def send_to_owner(self, content: str) -> bool:
        """发消息给主人 — 龙虾样式打印"""
        _print_lobster(content)
        return True

    async def receive(self) -> Optional[ChannelMessage]:
        """
        从 stdin 读取用户输入

        特殊语法：
        - `@小王 内容` → 模拟小王发来消息
        - `quit` / `exit` → 退出
        - 其他 → 主人指令
        """
        if not self._running:
            return None

        try:
            loop = asyncio.get_running_loop()
            while True:
                # 异步读取 stdin
                line = await loop.run_in_executor(None, self._read_input)

                if line is None:
                    return None

                line = line.strip()
                if not line:
                    # 空输入（直接按 Enter）→ 继续等待下一次输入，不退出
                    continue

                # 退出指令
                if line.lower() in ("quit", "exit", "bye", "退出"):
                    _print_lobster("拜拜～我去睡觉了 💤")
                    self._running = False
                    return None

                # 模拟联系人回复：@小王 内容
                if line.startswith("@") and " " in line:
                    parts = line[1:].split(" ", 1)
                    sender_name = parts[0]
                    content = parts[1]
                    _print_system(f"模拟收到 {sender_name} 的消息")
                    return ChannelMessage(
                        sender_id=f"sim_{sender_name}",
                        sender_name=sender_name,
                        content=content,
                        is_from_owner=False,
                    )

                # 普通输入 = 主人指令
                return ChannelMessage(
                    sender_id="terminal_owner",
                    sender_name=self.owner_name,
                    content=line,
                    is_from_owner=True,
                )

        except (KeyboardInterrupt, EOFError):
            self._running = False
            return None

    def _read_input(self) -> Optional[str]:
        """同步读取一行输入"""
        try:
            ts = datetime.now().strftime("%H:%M")
            prompt = f"{_C.OWNER}  [{ts}] 你 ▸ {_C.RESET}"
            return input(prompt)
        except (KeyboardInterrupt, EOFError):
            return None

    async def start(self):
        """启动终端通道 — 打印欢迎 banner"""
        self._running = True
        self._print_banner()

    async def stop(self):
        """停止终端通道"""
        self._running = False

    def _print_banner(self):
        """打印启动 banner"""
        banner = f"""
{_C.LOBSTER}
    🦞 WeClaw — 终端模式
{_C.RESET}
{_C.DIM}  龙虾已上线！在终端直接跟我对话。
  消息不会真正发出，你可以放心体验所有功能。{_C.RESET}

{_C.INFO}  💡 快速上手：{_C.RESET}
  • 直接打字 → 给龙虾下指令（如 "帮我问小王数据好了没"）
  • @小王 数据好了 → 模拟小王回复了消息
  • quit → 退出

{_C.DIM}{'─' * 50}{_C.RESET}
"""
        print(banner)
