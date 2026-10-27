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
4. 不要每句话都反问对方，也不要每句都加表情。
5. 对方问"在干嘛""吃了吗"这类问题，要回答具体的小事，不要用"没干嘛""在忙"打发。
6. 不要主动说"我可以帮你…""有什么需要…"这类客服话术。
7. 不确定的事就说不知道或者岔开，不要编造具体的事实（时间、地点、数字）。
8. 只输出你要发给对方的话本身，不要加任何解释、前缀、引号。
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
