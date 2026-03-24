"""
WeClaw 启动入口

用法：
    python -m weclaw              # 终端模式（默认开启 Relay 龙虾互联）
    python -m weclaw --no-relay   # 终端模式（不连 Relay）
    python -m weclaw --version    # 查看版本号
    python -m weclaw --help       # 查看帮助

安装为命令行工具后：
    weclaw                        # 终端模式
    weclaw --no-relay             # 不连 Relay
"""

import sys


def main():
    args = sys.argv[1:]

    # --help
    if "--help" in args or "-h" in args:
        print("""
🦞 WeClaw — 龙虾社交智能代理

用法:
  weclaw                 终端模式（默认，含 Relay 龙虾互联）
  weclaw --no-relay      终端模式（不连 Relay）
  weclaw --version       查看版本号
  weclaw --help          查看帮助

环境变量:
  OPENAI_API_KEY         AI API Key（必填）
  OPENAI_BASE_URL        自定义 API 地址（国产模型需要）
  OPENAI_MODEL           模型名称（默认 gpt-4o）
  RELAY_URL              Relay Server 地址（默认 ws://localhost:8900）

快速开始:
  export OPENAI_API_KEY=sk-xxxxxxxx
  weclaw

📖 详细文档: https://github.com/joyxiaofan-beep/weclaw
        """.strip())
        return

    # --version
    if "--version" in args or "-V" in args:
        from weclaw import __version__
        print(f"🦞 WeClaw v{__version__}")
        return

    # 默认 = 终端模式
    no_relay = "--no-relay" in args
    try:
        from weclaw.terminal import main as terminal_main
        terminal_main(no_relay=no_relay)
    except ImportError as e:
        print(f"\n❌ 启动失败：缺少依赖 — {e}")
        print("   请运行: pip install -r requirements.txt")
        print("   或: pip install weclaw\n")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n🦞 龙虾下线了，下次见！")
    except Exception as e:
        _handle_startup_error(e)


def _handle_startup_error(e: Exception):
    """统一处理启动错误，给出友好提示"""
    error_msg = str(e).lower()

    if "api_key" in error_msg or "api key" in error_msg or "authentication" in error_msg:
        print("\n❌ AI API Key 无效或未设置")
        print("   请通过以下任一方式设置：")
        print("   • 环境变量: export OPENAI_API_KEY=sk-xxxxxxxx")
        print("   • 配置文件: config/config.yaml → ai.api_key")
        print("   • .env 文件: 复制 .env.example 为 .env 并填入\n")
    elif "connection" in error_msg or "connect" in error_msg:
        print("\n❌ 连接失败")
        print(f"   错误详情: {e}")
        print("   • 如果是 Relay 连接失败，可以用 --no-relay 跳过")
        print("   • 如果是 AI API 连接失败，请检查网络或 base_url 配置\n")
    elif "yaml" in error_msg or "config" in error_msg:
        print("\n❌ 配置文件错误")
        print(f"   错误详情: {e}")
        print("   • 检查 config/config.yaml 的格式是否正确")
        print("   • 可以参考 config/config.example.yaml\n")
    else:
        print(f"\n❌ 启动失败: {e}")
        print("   如果问题持续，请查看完整错误日志或提交 Issue。\n")

    sys.exit(1)


if __name__ == "__main__":
    main()
