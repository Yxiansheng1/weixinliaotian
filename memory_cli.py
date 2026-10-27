"""多用户记忆管理。

    python memory_cli.py list                     # 有记忆的所有人
    python memory_cli.py stats                    # 总量
    python memory_cli.py show 张三 --limit 20     # 看某个人最近聊了什么
    python memory_cli.py summary 张三             # 只看摘要
    python memory_cli.py summarize 张三 --force   # 立刻压缩
    python memory_cli.py export 张三 --out 张三.md
    python memory_cli.py persona 张三 personas/lover.md   # 给这个人单独换角色卡
    python memory_cli.py persona 张三 -           # 取消单独角色卡
    python memory_cli.py rename 旧昵称 新昵称      # 微信改名后迁移记忆
    python memory_cli.py clear 张三 --yes         # 清空对话，保留档案
    python memory_cli.py delete 张三 --yes        # 连档案一起删

机器人运行时也能用：只读命令（list/show/stats/export）随时跑；
clear/delete/rename 会改文件，建议先停机器人。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from bot import config
from bot.llm import ChatLLM
from bot.memory import MemoryStore


def _fmt(ts) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


def _need(s: MemoryStore, chat: str) -> bool:
    if not s.stats(chat)["messages"] and not s.stats(chat)["bytes"]:
        print(f"没有 {chat} 的记忆。先跑 python memory_cli.py list 看有哪些人。")
        return False
    return True


def cmd_list(s: MemoryStore, args) -> int:
    names = s.chats()
    if not names:
        print("还没有任何记忆。")
        return 0
    print(f"{'昵称':<20}{'消息':>6}{'摘要':>6}{'最近活动':>18}  角色卡")
    print("-" * 78)
    for n in names:
        st = s.stats(n)
        print(
            f"{n[:18]:<20}{st['messages']:>6}"
            f"{('有' if st['has_summary'] else '-'):>7}"
            f"{_fmt(st['last_seen']):>19}  {s.get_persona(n) or '(默认)'}"
        )
    print(f"\n共 {len(names)} 人")
    return 0


def cmd_stats(s: MemoryStore, args) -> int:
    ov = s.overview()
    print(f"聊天对象 {ov['chats']} 人｜消息 {ov['messages']} 条｜占用 {ov['bytes']/1024:.0f} KB")
    for r in ov["rows"]:
        print(f"  {r['chat'][:18]:<20} {r['messages']:>4} 条  摘要 {r['summary_chars']:>4} 字")
    return 0


def cmd_show(s: MemoryStore, args) -> int:
    if not _need(s, args.chat):
        return 1
    st = s.stats(args.chat)
    print(f"===== {args.chat} =====")
    print(f"消息 {st['messages']} 条｜首次 {_fmt(st['first_seen'])}｜最近 {_fmt(st['last_seen'])}")
    print(f"文件 {st['file']}")
    print(f"角色卡 {s.get_persona(args.chat) or '(默认)'}")

    data = s.load(args.chat)
    if (data.get("summary") or "").strip():
        print("\n--- 记忆摘要 ---")
        print(data["summary"].strip())

    msgs = data["messages"]
    if args.limit:
        msgs = msgs[-args.limit :]
    if msgs:
        print(f"\n--- 最近 {len(msgs)} 条 ---")
        for m in msgs:
            who = "我" if m["role"] == "assistant" else args.chat
            print(f"[{who}] {m['content']}")
    return 0


def cmd_summary(s: MemoryStore, args) -> int:
    if not _need(s, args.chat):
        return 1
    data = s.load(args.chat)
    text = (data.get("summary") or "").strip()
    print(text if text else "（还没有摘要。消息超过阈值会自动生成，也可以 summarize --force 立刻生成）")
    return 0


def cmd_summarize(s: MemoryStore, args) -> int:
    if not _need(s, args.chat):
        return 1
    cfg = config.load(args.config)
    if not cfg.api_key:
        print("没读到 API Key，先建 .env")
        return 1
    llm = ChatLLM(cfg.llm, cfg.api_key)
    ok = s.summarize(args.chat, llm, force=args.force)
    if ok:
        print("已压缩。新的摘要：\n")
        print((s.load(args.chat)["summary"] or "").strip())
        return 0
    print("消息还不够多，没有压缩。加 --force 可以强制压一半。")
    return 0


def cmd_export(s: MemoryStore, args) -> int:
    if not _need(s, args.chat):
        return 1
    md = s.export_markdown(args.chat)
    if args.out:
        p = Path(args.out)
        p.write_text(md, encoding="utf-8")
        print(f"已导出到 {p.resolve()}")
    else:
        print(md)
    return 0


def cmd_persona(s: MemoryStore, args) -> int:
    if args.file == "-":
        s.set_persona(args.chat, "")
        print(f"{args.chat} 已恢复使用默认角色卡")
        return 0
    p = Path(args.file)
    if not p.exists():
        print(f"文件不存在：{p}")
        return 1
    cfg = config.load(args.config)
    rel = str(p.resolve().relative_to(cfg.root)).replace("\\", "/") if p.resolve().is_relative_to(cfg.root) else str(p.resolve())
    s.set_persona(args.chat, rel)
    print(f"{args.chat} 的角色卡已设为 {rel}（重启机器人后生效）")
    return 0


def cmd_rename(s: MemoryStore, args) -> int:
    if s.rename(args.old, args.new):
        print(f"记忆已从 {args.old} 迁到 {args.new}")
        return 0
    print(f"没有 {args.old} 的记忆")
    return 1


def cmd_clear(s: MemoryStore, args) -> int:
    if not args.yes:
        print(f"要清空 {args.chat} 的所有对话和摘要吗？加 --yes 确认。")
        return 1
    if s.clear(args.chat):
        print(f"{args.chat} 的记忆已清空（档案保留）")
        return 0
    print(f"没有 {args.chat} 的记忆")
    return 1


def cmd_delete(s: MemoryStore, args) -> int:
    if not args.yes:
        print(f"要彻底删除 {args.chat} 的记忆文件吗？加 --yes 确认。")
        return 1
    if s.delete(args.chat):
        print(f"{args.chat} 的记忆文件已删除")
        return 0
    print(f"没有 {args.chat} 的记忆")
    return 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="微信机器人记忆管理")
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出所有有记忆的人").set_defaults(func=cmd_list)
    sub.add_parser("stats", help="总量统计").set_defaults(func=cmd_stats)

    p = sub.add_parser("show", help="看某个人的记忆")
    p.add_argument("chat")
    p.add_argument("--limit", type=int, default=20, help="显示最近多少条，0 表示全部")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("summary", help="只看摘要")
    p.add_argument("chat")
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("summarize", help="立刻压缩历史")
    p.add_argument("chat")
    p.add_argument("--force", action="store_true", help="消息不够多也强制压一半")
    p.set_defaults(func=cmd_summarize)

    p = sub.add_parser("export", help="导出成 markdown")
    p.add_argument("chat")
    p.add_argument("--out", default=None, help="输出文件，不给就直接打印")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("persona", help="给某个人单独指定角色卡")
    p.add_argument("chat")
    p.add_argument("file", help="角色卡路径；用 - 取消")
    p.set_defaults(func=cmd_persona)

    p = sub.add_parser("rename", help="微信改昵称后迁移记忆")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("clear", help="清空对话（保留档案）")
    p.add_argument("chat")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("delete", help="删除整个记忆文件")
    p.add_argument("chat")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    args = ap.parse_args()
    cfg = config.load(args.config)
    s = MemoryStore(
        cfg.data_dir,
        max_turns=int(cfg.memory.get("max_turns", 40)),
        summarize_after=int(cfg.memory.get("summarize_after", 100)),
    )
    return args.func(s, args)


if __name__ == "__main__":
    sys.exit(main())
