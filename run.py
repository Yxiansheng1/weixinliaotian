"""启动入口。

    python run.py --demo      # 不碰微信，在终端里扮演对方，验证人设和模型
    python run.py             # 按 config.yaml 跑（默认 dry_run，不真发）
    python run.py --live      # 真发消息（危险，先看 README）
"""

from __future__ import annotations

import argparse
import sys

from bot import config
from bot import log as logmod
from bot.engine import BotEngine
from bot.llm import ChatLLM
from bot.memory import MemoryStore
from bot.wechat_client import ConsoleIO, WeChatIO

BANNER = r"""
 ┌─────────────────────────────────────────────┐
 │  微信陪聊机器人                             │
 │  个人微信自动化有封号风险，请使用小号测试   │
 └─────────────────────────────────────────────┘
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="微信陪聊机器人")
    p.add_argument("--config", default=None, help="配置文件路径，默认 config.yaml")
    p.add_argument("--demo", action="store_true", help="终端演示模式，不连接微信")
    p.add_argument("--live", action="store_true", help="强制真发（覆盖配置里的 dry_run）")
    p.add_argument("--name", default=None, help="演示模式下扮演的对方昵称")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    s = config.load(args.config)

    if args.live:
        s.raw["mode"] = "live"

    logmod.setup(s.raw.get("log_level", "INFO"), s.data_dir / "logs")

    problems = config.validate(s, require_targets=not args.demo)
    if problems:
        print("\n启动前有 %d 个问题需要先处理：\n" % len(problems))
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}\n")
        return 2

    print(BANNER)
    if s.live:
        print("  ⚠  当前是 LIVE 模式，回复会真的发到微信。Ctrl+C 可随时停止。\n")
    else:
        print("  当前是 DRY-RUN：只打印，不发送。确认效果后再改 config.yaml 的 mode。\n")

    llm = ChatLLM(s.llm, s.api_key)

    if args.demo:
        demo_name = args.name or (s.contacts[0] if s.contacts else "测试好友")
        if not s.targets():
            s.raw["listen"]["contacts"] = [demo_name]
        io = ConsoleIO(demo_name)
    else:
        try:
            io = WeChatIO()
        except Exception as e:
            print("连接微信失败：", e)
            print("\n常见原因：")
            print("  1. 微信主窗口没打开（点右下角托盘图标，把聊天窗口点出来）")
            print("  2. 微信没登录，或登录的是小号但窗口没开")
            print("  3. wxauto4 版本和你的微信客户端版本差太多")
            return 3

    memory = MemoryStore(
        s.data_dir,
        max_turns=int(s.memory.get("max_turns", 24)),
        summarize_after=int(s.memory.get("summarize_after", 48)),
    )

    engine = BotEngine(s, io, llm, memory)
    engine.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
