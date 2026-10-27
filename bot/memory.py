"""会话记忆：每个聊天对象一个 JSON 文件，滑动窗口 + 定期摘要。

为什么不用全量历史：角色扮演跑够一个月，上下文会撑爆并且开始胡说。
保留"摘要 + 最近若干轮原文"是性价比最高的做法。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

_SAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name).strip("_")[:60] or "unknown"


class MemoryStore:
    def __init__(self, data_dir: Path, max_turns: int = 24, summarize_after: int = 48):
        self.dir = Path(data_dir) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_turns = max_turns
        self.summarize_after = summarize_after

    # ---------- 读写 ----------
    def _path(self, chat: str) -> Path:
        return self.dir / f"{_safe_name(chat)}.json"

    def load(self, chat: str) -> dict:
        p = self._path(chat)
        if not p.exists():
            return {"chat": chat, "summary": "", "messages": []}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("summary", "")
            data.setdefault("messages", [])
            return data
        except Exception:
            log.exception("记忆文件损坏，已备份并重建：%s", p)
            p.rename(p.with_suffix(".broken.json"))
            return {"chat": chat, "summary": "", "messages": []}

    def save(self, chat: str, data: dict) -> None:
        p = self._path(chat)
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        tmp.replace(p)

    # ---------- 写一条 ----------
    def append(self, chat: str, role: str, content: str) -> None:
        data = self.load(chat)
        data["messages"].append(
            {"role": role, "content": content, "ts": int(time.time())}
        )
        self.save(chat, data)

    # ---------- 拼给模型的历史 ----------
    def messages_for(self, chat: str) -> list[dict]:
        data = self.load(chat)
        out: list[dict] = []
        if data["summary"]:
            out.append(
                {
                    "role": "system",
                    "content": "以下是你们之前聊过的事情的摘要（供你保持连续性，不要直接复述）：\n"
                    + data["summary"],
                }
            )
        keep = self.max_turns * 2  # 一问一答算两轮
        for m in data["messages"][-keep:]:
            out.append({"role": m["role"], "content": m["content"]})
        return out

    # ---------- 压缩 ----------
    def needs_summary(self, chat: str) -> bool:
        return len(self.load(chat)["messages"]) > self.summarize_after

    def summarize(self, chat: str, llm) -> None:
        data = self.load(chat)
        msgs = data["messages"]
        if len(msgs) <= self.summarize_after:
            return

        cut = len(msgs) // 2
        old, rest = msgs[:cut], msgs[cut:]

        lines = []
        for m in old:
            who = "我" if m["role"] == "assistant" else data["chat"]
            lines.append(f"{who}：{m['content']}")
        text = "\n".join(lines)

        if data["summary"]:
            text = f"（更早的摘要：{data['summary']}）\n" + text

        try:
            new_summary = llm.summarize(text)
        except Exception:
            log.exception("摘要失败，跳过本轮压缩（不影响回复）")
            return

        data["summary"] = new_summary
        data["messages"] = rest
        self.save(chat, data)
        log.info("已压缩 %s 的历史：%d 条 → 摘要 + %d 条", chat, len(old), len(rest))
