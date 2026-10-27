"""大模型调用。走 OpenAI 兼容协议，DeepSeek / Kimi / 通义 / OpenAI 都能用。"""

from __future__ import annotations

import logging
import re

from openai import OpenAI

log = logging.getLogger(__name__)

SUMMARY_PROMPT = """把下面这段微信聊天记录压缩成不超过 180 字的事实摘要。

要求：
- 只保留：关系进展、对方提到的重要信息（工作/生活/情绪/约定）、说好要做的事
- 用第三人称陈述，不写"我们""你"
- 不要评论，不要心理分析，不要建议
- 直接输出摘要正文

聊天记录：
{history}"""


class ChatLLM:
    def __init__(self, cfg: dict, api_key: str):
        self.cfg = cfg
        self.model = cfg.get("model", "deepseek-chat")
        self.client = OpenAI(
            api_key=api_key,
            base_url=cfg.get("base_url") or None,
            timeout=cfg.get("timeout", 60),
            max_retries=2,
        )

    def reply(self, messages: list[dict]) -> str:
        """生成一条回复。失败抛异常，由上层决定怎么兜底。"""
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": float(self.cfg.get("temperature", 1.1)),
            "max_tokens": int(self.cfg.get("max_tokens", 300)),
        }
        # 中转站对"多出来的参数"容忍度不一，个别会直接 400，做成可关
        if self.cfg.get("penalties", True):
            params["presence_penalty"] = float(self.cfg.get("presence_penalty", 0.3))
            params["frequency_penalty"] = float(self.cfg.get("frequency_penalty", 0.3))

        resp = self.client.chat.completions.create(**params)
        text = (resp.choices[0].message.content or "").strip()
        return text

    def summarize(self, history_text: str) -> str:
        """把旧对话压成摘要，用于长期记忆。"""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "user", "content": SUMMARY_PROMPT.format(history=history_text)}
            ],
            temperature=0.3,
            max_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()

    def list_models(self) -> list[str]:
        """列出这个接口下可用的模型名。中转站的模型名常和官方不一样，得实测。"""
        try:
            page = self.client.models.list()
            return sorted({m.id for m in page.data})
        except Exception:
            # 有些中转不实现 /models，用原生请求再试一次
            resp = self.client._client.get("/models")
            data = resp.json()
            items = data.get("data", data if isinstance(data, list) else [])
            return sorted({str(m.get("id")) for m in items if m.get("id")})


# ---------- 输出清洗：把模型偶尔冒出来的"AI 味"剪掉 ----------

_PREFIX = re.compile(r"^[（(]?(我|阿澈|回复)?[）)]?\s*[:：]\s*")
_MD = re.compile(r"(\*\*|__|\*|`{1,3}|^#{1,6}\s+|^[-*+]\s+|^\d+[.、]\s+)", re.MULTILINE)
_QUOTES = re.compile(r'^["“”「」『』\'](.*)["“”「」『』\']$', re.DOTALL)
_ACTION = re.compile(r"[（(][^（()）]{1,20}[）)]")


def clean(text: str, allow_actions: bool = False) -> str:
    t = text.strip()
    # 去掉模型自己加的说话人前缀
    t = _PREFIX.sub("", t, count=1)
    # 去掉整段包裹的引号
    m = _QUOTES.match(t)
    if m:
        t = m.group(1).strip()
    if not allow_actions:
        t = _ACTION.sub("", t)
    t = _MD.sub("", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t
