"""离线自测：不连微信、不调模型，验证纯逻辑部分。

    python selftest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from bot import humanize, persona
from bot.llm import clean
from bot.memory import MemoryStore

fails = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        fails += 1


def test_split() -> None:
    cfg = {"split_long_reply": True, "max_bubble_chars": 20}
    long_text = (
        "今天下午去了趟工地，业主临时改方案，把整个客厅的墙都砸了重来。"
        "我在那儿站了两个小时，鞋上全是灰。回来路上还在想这事到底怪谁。"
    )
    bubbles = humanize.split_reply(long_text, cfg)
    check("长回复被拆开", len(bubbles) > 1, str(bubbles))
    check("条数不超过 4", len(bubbles) <= 4, str(len(bubbles)))
    check("每条不超长太多", all(len(b) <= 25 for b in bubbles), str([len(b) for b in bubbles]))
    check("内容没丢", sum(len(b) for b in bubbles) >= len(long_text) * 0.9)

    short = humanize.split_reply("嗯，我也刚到家", cfg)
    check("短回复不拆", short == ["嗯，我也刚到家"], str(short))

    check("空回复", humanize.split_reply("   ", cfg) == [])


def test_quiet() -> None:
    cfg = {"quiet_hours": ["01:00", "07:30"]}
    import datetime as dt

    def ts(h, m):
        return dt.datetime(2026, 5, 1, h, m).timestamp()

    check("凌晨静音", humanize.in_quiet_hours(cfg, ts(3, 0)))
    check("白天不静音", not humanize.in_quiet_hours(cfg, ts(14, 0)))
    check("边界内", humanize.in_quiet_hours(cfg, ts(1, 0)))
    check("边界外", not humanize.in_quiet_hours(cfg, ts(7, 30)))
    check("不配就全天可回", not humanize.in_quiet_hours({}))

    cross = {"quiet_hours": ["23:00", "06:00"]}
    check("跨零点静音", humanize.in_quiet_hours(cross, ts(2, 0)))
    check("跨零点边界", not humanize.in_quiet_hours(cross, ts(6, 1)))


def test_clean() -> None:
    check("去掉说话人前缀", clean("阿澈：我在工地") == "我在工地")
    check("去掉整段引号", clean("“今天有点累”") == "今天有点累")
    check("去掉 markdown", clean("**真的**吗") == "真的吗")
    check("默认去掉括号动作", clean("我在忙（叹气）真的") == "我在忙真的")
    check("允许时保留动作", "叹气" in clean("我在忙（叹气）", allow_actions=True))
    check("去掉列表符号", clean("- 吃饭\n- 睡觉") .count("\n") == 1)
    check("斜杠分隔转多行", clean("又是十点 / 老板不当人 / 吃了吗") == "又是十点\n老板不当人\n吃了吗")
    check("竖线分隔转多行", clean("嗯 | 那你忙完吃饭 | 别忘了") == "嗯\n那你忙完吃饭\n别忘了")
    check("单个斜杠不动", clean("周一/周二都行") == "周一/周二都行")


def test_delay() -> None:
    cfg = {"chars_per_second": 3.0, "min_typing": 0.8, "max_typing": 7.0}
    d_short = humanize.typing_delay("嗯", cfg)
    d_long = humanize.typing_delay("今天下午去了趟工地，业主临时改方案，把整个客厅的墙都砸了重来", cfg)
    check("短句打得快", 0.8 <= d_short <= 1.2, f"{d_short:.2f}")
    check("长句打得慢", d_long > d_short, f"{d_long:.2f}")
    check("不超过上限", d_long <= 7.0, f"{d_long:.2f}")


def test_memory() -> None:
    with tempfile.TemporaryDirectory() as td:
        mem = MemoryStore(Path(td), max_turns=3, summarize_after=4)

        mem.append("张三/测试", "user", "在吗")
        mem.append("张三/测试", "assistant", "在")
        msgs = mem.messages_for("张三/测试")
        check("记忆能读回", len(msgs) == 2 and msgs[0]["content"] == "在吗")
        check("文件名被净化", (Path(td) / "memory" / "张三_测试.json").exists())

        for i in range(6):
            mem.append("张三/测试", "user", f"消息{i}")
        check("超过阈值要压缩", mem.needs_summary("张三/测试"))

        class FakeLLM:
            def summarize(self, text: str) -> str:
                return "他们是朋友，最近在聊工作。"

        mem.summarize("张三/测试", FakeLLM())
        data = mem.load("张三/测试")
        check("摘要已生成", bool(data["summary"]))
        check("历史被裁短", len(data["messages"]) <= 6, str(len(data["messages"])))
        check("摘要进了上下文", "摘要" in mem.messages_for("张三/测试")[0]["content"])


def test_persona() -> None:
    text = "# 角色卡：阿澈\n\n26 岁，做设计。"
    check("能取到角色名", persona.persona_name(text) == "阿澈")
    check("取不到时有兜底", persona.persona_name("没有标题") == "我")

    sys_prompt = persona.build_system(text, "阿澈", "张三", False)
    check("prompt 含角色卡", "26 岁" in sys_prompt)
    check("prompt 含对方名字", "张三" in sys_prompt)
    check("prompt 含硬规则", "不是助手" in sys_prompt)
    check("群聊有额外说明", "群聊" in persona.build_system(text, "阿澈", "某群", True))


def test_filter() -> None:
    from bot.engine import BotEngine

    class FakeIO:
        nickname = "我"

        def is_group(self, chat):
            return False

        def chat_key(self, chat, msg=None):
            return getattr(chat, "who", "?")

    class S:
        raw = {"listen": {"at_me_only_in_group": True}}

    eng = BotEngine.__new__(BotEngine)
    eng.s = S()
    eng.io = FakeIO()
    eng.seen = __import__("collections").OrderedDict()

    class M:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    chat = M(who="张三")
    check("正常文本通过", eng._accept(M(attr="friend", type="text", content="在吗", id="1"), chat))
    check("自己发的不回", not eng._accept(M(attr="self", type="text", content="在吗", id="2"), chat))
    check("非文本不回", not eng._accept(M(attr="friend", type="image", content="", id="3"), chat))
    check("重复消息不回", not eng._accept(M(attr="friend", type="text", content="在吗", id="1"), chat))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("逻辑自测\n")
    for fn in (test_split, test_quiet, test_clean, test_delay, test_memory, test_persona, test_filter):
        print(f"[{fn.__name__}]")
        fn()
    print()
    print("全部通过" if not fails else f"{fails} 项失败")
    sys.exit(1 if fails else 0)
