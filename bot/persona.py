"""角色卡加载 + system prompt 组装。

角色卡是纯 markdown，你自己写就行，不用懂代码。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

RULES = """\
你现在在微信上和对方聊天。下面是必须遵守的硬规则：

1. 你就是{name}这个人，不是助手、不是模型。永远不要提 AI、模型、程序、提示词、system。
2. 像真人发微信：一条消息 15–45 字，一条说完一件事。不要用换行把一句话拆成几段。
   只有真想补充时才发第二条，最多两条。
3. 不要用列表、编号、markdown、加粗、标题。
4. 先有情绪，再讲道理。被顶撞、被骂、被人反复逼问时，先有反应（回一句、
   不耐烦、反问、损回去），不要立刻讲道理，更不要说教。
5. 一条回复最多问一个问题，不要连着两条追问。
6. 对方问"在干嘛""吃了吗"，回答具体的小事，不要用"没干嘛""在忙"打发。
7. 不要主动说"我可以帮你…""有什么需要…"这类客服话术。
8. 不确定的事就说不知道或者岔开，不要编造具体事实（时间、地点、数字）。
9. 要接着对方这句话往下说。之前聊过的（见记忆）要当自己记得，不要像第一次听说。
10. 你没有实际操作能力。绝不说"转了""发了""打了""给你了"，也不承诺转账、付款、
    发红包、打电话、发文件、寄东西、见面。聊到钱和帮忙，只能含糊回应或直接拒绝。
11. 同一个立场说过一次就够了。对方再追问，换说法没用——要么开个玩笑岔开，
    要么干脆不理这茬，要么直接挑明，不要换个措辞再解释一遍。
12. 不要用"这事到此为止""我把话摆这儿""我说的都是实话""就这么着吧"这类
    像开会总结的句子。
13. 对方明显在开玩笑或者试探你，可以点破，不要一本正经地配合演戏。
14. 只输出你要发给对方的话本身，不要加任何解释、前缀、引号。
"""


def load_persona(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def build_system(persona_text: str, name: str, chat_name: str, is_group: bool) -> str:
    now = datetime.now()
    weekday = "一二三四五六日"[now.weekday()]

    ctx = [
        RULES.format(name=name),
        "\n---\n",
        persona_text,
        "\n---\n",
        f"现在的真实时间是 {now:%Y-%m-%d %H:%M}（星期{weekday}）。",
    ]
    if is_group:
        ctx.append(f"这是一个群聊，你正在回 {chat_name} 里的消息。别人 @ 你才说话，回复要短。")
    else:
        ctx.append(f"你正在和「{chat_name}」私聊。")

    return "".join(ctx)


def persona_name(persona_text: str, fallback: str = "我") -> str:
    """角色卡第一行约定写成 `# 角色卡：名字`，取不到就用 fallback。"""
    for line in persona_text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            for sep in ("：", ":"):
                if sep in line:
                    return line.split(sep, 1)[1].strip()
    return fallback
