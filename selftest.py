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
    cfg = {"split_long_reply": True, "max_bubble_chars": 20, "max_bubbles": 4}
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

    # 模型用换行分条：短文本也必须拆成多条，且气泡里不能残留换行
    multi = "第三遍？\n\n说真的，改到第三遍基本就是他自己没想清楚\n你是真考虑过，还是被气到了"
    mb = humanize.split_reply(multi, cfg)
    check("换行分条被保留", len(mb) == 3, str(mb))
    check("气泡不含换行", all("\n" not in b for b in mb), str(mb))
    check("分条后不丢字", sum(len(b) for b in mb) == len(multi.replace("\n", "")), str(mb))

    # 超过上限不能丢内容
    many = "\n".join([f"第{i}句话在这里" for i in range(1, 8)])
    mm = humanize.split_reply(many, cfg)
    check("超上限条数受限", len(mm) == 4, str(len(mm)))
    check("超上限不丢字", sum(len(b) for b in mm) == len(many.replace("\n", "")), str(mm))

    # 关掉拆句时，应压成一条且没有换行
    flat = humanize.split_reply(multi, {"split_long_reply": False})
    check("关闭拆句只有一条", len(flat) == 1, str(flat))
    check("关闭拆句也无换行", "\n" not in flat[0], repr(flat))


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
        check("摘要进了上下文", "最近在聊工作" in mem.messages_for("张三/测试")[0]["content"])


def test_memory_admin() -> None:
    """多用户管理：列表、统计、清空、改名、导出、按人设。"""
    import tempfile as _tf

    from bot.memory import MemoryStore as MS

    with _tf.TemporaryDirectory() as td:
        s = MS(Path(td), max_turns=5, summarize_after=10)

        check("空库没有聊天", s.chats() == [])

        s.append("张三", "user", "在吗")
        s.append("张三", "assistant", "在")
        s.append("小满", "user", "今天好累")
        s.append("小满", "assistant", "那你早点睡")

        names = s.chats()
        check("两个人都在列表里", set(names) == {"张三", "小满"}, str(names))

        st = s.stats("张三")
        check("统计消息数", st["messages"] == 2, str(st))
        check("统计文件存在", st["bytes"] > 0)

        ov = s.overview()
        check("总览汇总正确", ov["chats"] == 2 and ov["messages"] == 4, str(ov))

        # 每人独立：清空一个不影响另一个
        s.clear("张三")
        check("清空后消息归零", s.stats("张三")["messages"] == 0)
        check("另一个人不受影响", s.stats("小满")["messages"] == 2)
        check("清空保留档案", s.stats("张三")["first_seen"] is not None)

        # 改名迁移
        s.append("小满", "user", "睡了吗")
        check("改名成功", s.rename("小满", "小满满"))
        check("新名字有记忆", s.stats("小满满")["messages"] == 3)
        check("旧名字没了", "小满" not in s.chats(), str(s.chats()))

        # 单独人设
        s.set_persona("小满满", "personas/lover.md")
        check("人设记住了", s.get_persona("小满满") == "personas/lover.md")
        s.set_persona("小满满", "")
        check("人设能取消", s.get_persona("小满满") == "")

        # 导出
        md = s.export_markdown("小满满")
        check("导出含标题", "小满满" in md)
        check("导出含对话", "今天好累" in md)

        # 删除
        check("删除成功", s.delete("小满满"))
        check("删除后不在列表", "小满满" not in s.chats(), str(s.chats()))

        # 特殊昵称能安全落盘
        s.append("a/b:c*d", "user", "测试")
        check("含符号的昵称也能存", "a/b:c*d" in s.chats(), str(s.chats()))


def test_cursor() -> None:
    """新消息判定：id 不可靠、列表会裁剪，只能靠指纹序列滑动比对。"""
    from bot.engine import BotEngine
    class M:
        def __init__(self, attr, sender, content):
            self.attr = attr
            self.sender = sender
            self.content = content

    eng = BotEngine.__new__(BotEngine)
    eng.cursors = {}

    base = [M("system", "system", "11:11"), M("friend", "A", "在")]
    eng.cursors["A"] = (len(base), [eng._fp(m) for m in base[-3:]])

    check("没有新消息时不返回", eng._new_messages("A", list(base)) == [])

    more = base + [M("friend", "A", "吃了吗")]
    got = eng._new_messages("A", more)
    check("追加一条能识别", len(got) == 1 and got[0].content == "吃了吗", str([g.content for g in got]))

    # 头部被裁掉（旧消息滚出去），上次的尾巴仍在但不从 0 开始
    trimmed = more[1:] + [M("friend", "A", "在忙")]
    got2 = eng._new_messages("A", trimmed)
    check("列表被裁剪也能识别", len(got2) == 1 and got2[0].content == "在忙", str([g.content for g in got2]))

    # 连续两条一起到
    two = trimmed + [M("friend", "A", "睡了"), M("friend", "A", "晚安")]
    got3 = eng._new_messages("A", two)
    check("多条一起到都能识别", [g.content for g in got3] == ["睡了", "晚安"], str([g.content for g in got3]))

    # 完全对不上时不乱回
    weird = [M("friend", "A", "完全不同的内容")]
    got4 = eng._new_messages("A", weird)
    check("对不上时本轮不回", got4 == [], str([g.content for g in got4]))


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
    eng.seen_lock = __import__("threading").Lock()

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
    for fn in (test_split, test_quiet, test_clean, test_delay, test_memory,
               test_memory_admin, test_cursor, test_persona, test_filter):
        print(f"[{fn.__name__}]")
        fn()
    print()
    print("全部通过" if not fails else f"{fails} 项失败")
    sys.exit(1 if fails else 0)
