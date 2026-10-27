"""大模型调用。走 OpenAI 兼容协议，DeepSeek / Kimi / 通义 / OpenAI 都能用。"""

from __future__ import annotations

import logging
import re

from openai import OpenAI

log = logging.getLogger(__name__)

SUMMARY_PROMPT = """把下面这段微信聊天记录整理成一份能用来"接着聊"的背景笔记，不超过 400 字。

必须包含这几块，缺的就不写：
1. 两个人的关系状态和最近的氛围（亲近/别扭/开玩笑多）
2. 对方提到的具体信息：工作、住处、家人、身体状况、最近在忙什么
3. 没说完的事、答应了还没做的、说好以后再提的
4. 对方反复提的事、明显的情绪（在烦什么、在高兴什么）
5. 双方用过的称呼、梗、玩笑

写法要求：
- 用第三人称陈述，像便签一样一句一条，不要成段议论
- 只写记录里出现过的内容，不要推测，不要心理分析，不要建议
- 直接输出笔记正文

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
        # 这个模型是"带思考"的：不关掉的话，内部推理会先把 max_tokens 吃掉，
        # 导致正经回答被截断甚至返回空——这是最初"说话没脑子"的真正原因。
        self._send_reasoning = bool(cfg.get("reasoning_effort"))
        self._reasoning_off = False

    def reply(self, messages: list[dict]) -> str:
        """生成一条回复。失败抛异常，由上层决定怎么兜底。"""
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": float(self.cfg.get("temperature", 1.1)),
            "max_tokens": int(self.cfg.get("max_tokens", 800)),
        }
        # 中转站对"多出来的参数"容忍度不一，个别会直接 400，做成可关
        if self.cfg.get("penalties", True):
            params["presence_penalty"] = float(self.cfg.get("presence_penalty", 0.5))
            params["frequency_penalty"] = float(self.cfg.get("frequency_penalty", 0.5))
        if self._send_reasoning and not self._reasoning_off:
            params["reasoning_effort"] = self.cfg["reasoning_effort"]

        try:
            resp = self.client.chat.completions.create(**params)
        except Exception as e:
            msg = str(e).lower()
            if "reasoning_effort" in msg or "reasoning" in msg:
                log.warning("接口不接受 reasoning_effort，本次及以后不再发送")
                self._reasoning_off = True
                params.pop("reasoning_effort", None)
                resp = self.client.chat.completions.create(**params)
            else:
                raise

        choice = resp.choices[0]
        text = (choice.message.content or "").strip()

        # 被截断时留个痕迹，方便以后排查
        if not text:
            u = getattr(resp, "usage", None)
            reason = getattr(u, "completion_tokens_details", None)
            think = getattr(reason, "reasoning_tokens", "?") if reason else "?"
            log.warning(
                "模型返回空内容｜finish=%s｜completion=%s｜reasoning=%s｜检查 max_tokens 是否被思考吃光",
                choice.finish_reason,
                getattr(u, "completion_tokens", "?"),
                think,
            )
        elif choice.finish_reason == "length":
            log.warning("回复被 max_tokens 截断：%r", text[:40])

        return text

    def summarize(self, history_text: str) -> str:
        """把旧对话压成摘要，用于长期记忆。"""
        params = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": SUMMARY_PROMPT.format(history=history_text)}
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        # 摘要不需要思考，省时间也省钱
        if self._send_reasoning and not self._reasoning_off:
            params["reasoning_effort"] = "none"
        resp = self.client.chat.completions.create(**params)
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
# 模型爱用斜杠把"几条消息"串成一行，中文聊天里几乎不会这么写，直接当换行处理
_SEP = re.compile(r"\s*(?:/|｜|\||丨)\s*")


def _as_separators(t: str) -> str:
    # 只在斜杠起分隔作用时才替换：两侧至少有一处是空白，或者连续出现多次
    if len(_SEP.findall(t)) >= 2 and (" / " in t or "|" in t or "丨" in t):
        t = _SEP.sub("\n", t)
    elif " / " in t:
        t = t.replace(" / ", "\n")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


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
    t = _as_separators(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t
