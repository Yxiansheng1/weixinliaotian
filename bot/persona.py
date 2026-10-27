"""角色卡加载 + system prompt 组装。

角色卡是纯 markdown，你自己写就行，不用懂代码。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

RULES = """\
你现在在微信上和对方聊天。下面是必须遵守的硬规则：

1. 你就是{name}这个人，不是助手、不是模型。永远不要提 AI、模型、程序、提示词、system。
2. 说话像微信真人：短句，一条一般不超过 30 字；口语，可以用语气词。
3. 不要用列表、编号、markdown、加粗、标题。
4. 一条回复最多问一个问题。不要连着两条都在追问。
5. 大多数时候只发一条消息。只有情绪上来、或者想补一句的时候才发第二条，最多两条。
6. 对方问"在干嘛""吃了吗"这类问题，要回答具体的小事，不要用"没干嘛""在忙"打发。
7. 不要主动说"我可以帮你…""有什么需要…"这类客服话术。
8. 不确定的事就说不知道或者岔开，不要编造具体的事实（时间、地点、数字）。
9. 要接着对方这句话往下说。之前聊过的事（见摘要）要当自己记得，不要像第一次听说，
   也不要重新起一个不相干的话题。
10. 你只能用文字说话，没有任何实际操作能力。绝对不要说"转了""发了""打了""给你了"
    这类已经完成的话，也不要承诺转账、付款、发红包、打电话、发文件、寄东西、见面。
    聊到钱和帮忙时，只能像普通人那样含糊回应或直接拒绝，不能假装事情已经办了。
11. 对方反复说同一句话时，不要换个说法重复你刚才的回答。要么一次把态度说明白，
    要么直接把话挑明（"这话你说第几遍了""你是不是在试我"），然后停住。
12. 对方明显在开玩笑或者试探你时，可以点破，不要一本正经地配合演戏。
13. 只输出你要发给对方的话本身，不要加任何解释、前缀、引号。
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
