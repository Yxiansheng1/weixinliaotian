"""拟人化：延迟、拆句、发送节奏。

机器人被认出来的最快方式就是"秒回 + 一大段 + 排版工整"。
这里做的就是把回复打散成真人节奏。
"""

from __future__ import annotations

import random
import re
import time


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def read_delay(cfg: dict) -> float:
    lo, hi = cfg.get("read_delay", [0.6, 1.8])
    return random.uniform(float(lo), float(hi))


def typing_delay(text: str, cfg: dict) -> float:
    """按字数和打字速度算，加一点随机抖动。"""
    cps = max(0.5, float(cfg.get("chars_per_second", 3.2)))
    lo = float(cfg.get("min_typing", 0.8))
    hi = float(cfg.get("max_typing", 7.0))
    base = len(text) / cps
    jitter = random.uniform(-0.25, 0.45) * base
    return clamp(base + jitter, lo, hi)


def bubble_gap(cfg: dict) -> float:
    lo, hi = cfg.get("bubble_gap", [0.7, 1.8])
    return random.uniform(float(lo), float(hi))


def split_reply(text: str, cfg: dict) -> list[str]:
    """把长回复拆成若干条短消息，最多 4 条。"""
    text = text.strip()
    if not text:
        return []
    if not cfg.get("split_long_reply", True):
        return [text]

    max_chars = int(cfg.get("max_bubble_chars", 55))
    if len(text) <= max_chars:
        return [text]

    bubbles: list[str] = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            bubbles.append(para)
            continue

        # 先按句号类标点切，再退到逗号类，尽量不把一句话拦腰砍断
        sentences = re.findall(r"[^。！？!?…~，,；;]+[。！？!?…~，,；;]*", para) or [para]
        cur = ""
        for s in sentences:
            if len(cur) + len(s) <= max_chars:
                cur += s
            else:
                if cur:
                    bubbles.append(cur.strip())
                # 单句仍然超长，硬切
                while len(s) > max_chars:
                    bubbles.append(s[:max_chars])
                    s = s[max_chars:]
                cur = s
        if cur.strip():
            bubbles.append(cur.strip())

    # 合并过多的碎片
    merged: list[str] = []
    for b in bubbles:
        if merged and len(merged[-1]) + len(b) <= max_chars:
            merged[-1] = merged[-1] + b
        else:
            merged.append(b)

    return [b for b in merged if b][:4]


def in_quiet_hours(cfg: dict, now: float | None = None) -> bool:
    """quiet_hours: [\"01:00\", \"07:30\"]，跨零点也支持。"""
    qh = cfg.get("quiet_hours") or []
    if len(qh) != 2:
        return False

    t = time.localtime(now)
    cur = t.tm_hour * 60 + t.tm_min

    def parse(s: str) -> int:
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    start, end = parse(qh[0]), parse(qh[1])
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end
