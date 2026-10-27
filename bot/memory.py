"""会话记忆：每个聊天对象一个 JSON 文件，滑动窗口 + 定期摘要。

为什么不用全量历史：角色扮演跑够一个月，上下文会撑爆并且开始胡说。
保留"摘要 + 最近若干轮原文"是性价比最高的做法。

多用户：一个聊天一个文件，互不干扰。文件名由昵称净化得到，
中文、空格、斜杠都能安全落盘；昵称改了用 rename() 迁移。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_SAFE = re.compile(r'[\\/:*?"<>|\s]+')

# 同一个进程里多个工作线程会同时读写不同聊天的记忆
_LOCK = threading.RLock()


def _safe_name(name: str) -> str:
    return _SAFE.sub("_", name).strip("_")[:60] or "unknown"


def _fmt_ts(ts: float | int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")


class MemoryStore:
    def __init__(self, data_dir: Path, max_turns: int = 24, summarize_after: int = 48):
        self.dir = Path(data_dir) / "memory"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_turns = max_turns
        self.summarize_after = summarize_after

    # ---------- 读写 ----------
    def _path(self, chat: str) -> Path:
        return self.dir / f"{_safe_name(chat)}.json"

    def _blank(self, chat: str) -> dict:
        now = int(time.time())
        return {
            "chat": chat,
            "summary": "",
            "messages": [],
            "meta": {"first_seen": now, "last_seen": now, "persona": ""},
        }

    def load(self, chat: str) -> dict:
        p = self._path(chat)
        if not p.exists():
            return self._blank(chat)
        with _LOCK:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                log.exception("记忆文件损坏，已备份并重建：%s", p)
                try:
                    p.rename(p.with_suffix(".broken.json"))
                except Exception:
                    pass
                return self._blank(chat)

        data.setdefault("chat", chat)
        data.setdefault("summary", "")
        data.setdefault("messages", [])
        meta = data.setdefault("meta", {})
        meta.setdefault("first_seen", int(time.time()))
        meta.setdefault("last_seen", int(time.time()))
        meta.setdefault("persona", "")
        return data

    def save(self, chat: str, data: dict) -> None:
        p = self._path(chat)
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = data.setdefault("meta", {})
        meta["last_seen"] = int(time.time())
        with _LOCK:
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
                    "content": (
                        "这是你自己记得的、之前和对方聊过的事。把它当成你的记忆："
                        "该接的时候就自然接上，不要念出来，也不要问对方还记不记得。\n"
                        + data["summary"]
                    ),
                }
            )
        keep = self.max_turns * 2  # 一问一答算两轮
        for m in data["messages"][-keep:]:
            out.append({"role": m["role"], "content": m["content"]})
        return out

    # ---------- 压缩 ----------
    def needs_summary(self, chat: str) -> bool:
        return len(self.load(chat)["messages"]) > self.summarize_after

    def summarize(self, chat: str, llm, force: bool = False) -> bool:
        """把前半段历史压进摘要。返回是否真的压了。"""
        data = self.load(chat)
        msgs = data["messages"]
        if not msgs:
            return False
        if not force and len(msgs) <= self.summarize_after:
            return False

        cut = max(1, len(msgs) // 2) if force else len(msgs) // 2
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
            return False

        data["summary"] = new_summary
        data["messages"] = rest
        self.save(chat, data)
        log.info("已压缩 %s 的历史：%d 条 → 摘要 + %d 条", chat, len(old), len(rest))
        return True

    # ================= 多用户管理 =================

    def chats(self) -> list[str]:
        """列出所有有记忆的聊天对象（按最近活动倒序）。"""
        items = []
        for p in self.dir.glob("*.json"):
            if p.name.endswith((".broken.json", ".bak", ".tmp")):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            items.append((data.get("chat") or p.stem, data))
        items.sort(key=lambda x: x[1].get("meta", {}).get("last_seen", 0), reverse=True)
        return [name for name, _ in items]

    def stats(self, chat: str) -> dict:
        data = self.load(chat)
        msgs = data["messages"]
        p = self._path(chat)
        return {
            "chat": data["chat"],
            "messages": len(msgs),
            "summary_chars": len(data.get("summary") or ""),
            "has_summary": bool((data.get("summary") or "").strip()),
            "first_seen": data["meta"].get("first_seen"),
            "last_seen": data["meta"].get("last_seen"),
            "bytes": p.stat().st_size if p.exists() else 0,
            "file": str(p),
        }

    def overview(self) -> dict:
        names = self.chats()
        rows = [self.stats(n) for n in names]
        return {
            "chats": len(rows),
            "messages": sum(r["messages"] for r in rows),
            "bytes": sum(r["bytes"] for r in rows),
            "rows": rows,
        }

    def clear(self, chat: str) -> bool:
        """清空对话和摘要，保留这个人的档案（首次聊天时间等）。"""
        if not self._path(chat).exists():
            return False
        data = self.load(chat)
        data["messages"] = []
        data["summary"] = ""
        self.save(chat, data)
        log.info("已清空 %s 的记忆", chat)
        return True

    def delete(self, chat: str) -> bool:
        p = self._path(chat)
        with _LOCK:
            if p.exists():
                p.unlink()
                log.info("已删除 %s 的记忆文件", chat)
                return True
        return False

    def rename(self, old: str, new: str) -> bool:
        """微信里改了昵称时用这个把记忆迁过去。"""
        src = self._path(old)
        if not src.exists():
            return False
        data = self.load(old)
        data["chat"] = new
        self.save(new, data)
        dst = self._path(new)
        if dst != src:
            with _LOCK:
                src.unlink()
        log.info("记忆已从 %s 迁移到 %s", old, new)
        return True

    def set_persona(self, chat: str, persona_file: str) -> None:
        data = self.load(chat)
        data["meta"]["persona"] = persona_file
        self.save(chat, data)

    def get_persona(self, chat: str) -> str:
        return self.load(chat)["meta"].get("persona", "")

    def export_markdown(self, chat: str) -> str:
        data = self.load(chat)
        st = self.stats(chat)
        lines = [
            f"# 与 {chat} 的聊天记录",
            "",
            f"- 消息数：{st['messages']}",
            f"- 首次聊天：{_fmt_ts(st['first_seen'])}",
            f"- 最近一次：{_fmt_ts(st['last_seen'])}",
            "",
        ]
        if (data.get("summary") or "").strip():
            lines += ["## 记忆摘要", "", data["summary"], ""]
        lines += ["## 对话", ""]
        for m in data["messages"]:
            who = "我" if m["role"] == "assistant" else chat
            ts = _fmt_ts(m.get("ts"))
            body = m["content"].replace("\n", "  \n")
            lines.append(f"**{who}**（{ts}）：{body}")
            lines.append("")
        return "\n".join(lines)
