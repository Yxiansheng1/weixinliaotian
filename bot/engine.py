"""调度核心：收消息 → 判断该不该回 → 调模型 → 拟人化发送。"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
from collections import OrderedDict

from . import humanize, llm as llm_mod, persona
from .config import Settings

log = logging.getLogger(__name__)

MAX_SEEN = 500


class BotEngine:
    def __init__(self, s: Settings, io, llm, memory) -> None:
        self.s = s
        self.io = io
        self.llm = llm
        self.memory = memory

        self.persona_text = persona.load_persona(s.persona_path)
        self.persona_name = persona.persona_name(self.persona_text, s.persona_path.stem)

        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.locks: dict[str, threading.Lock] = {}
        self.stop_flag = threading.Event()
        self.replied = 0

    # ---------------- 生命周期 ----------------

    def attach(self) -> int:
        ok = 0
        for name in self.s.targets():
            if self.io.attach(name, self._on_message):
                ok += 1
        return ok

    def run_forever(self) -> None:
        attached = self.attach()
        if attached == 0:
            log.error(
                "没有任何聊天对象挂载成功，已退出。\n"
                "      检查 listen.contacts 里的昵称是否和微信里完全一致，"
                "以及微信主窗口是否打开。"
            )
            return

        for i in range(2):
            threading.Thread(
                target=self._worker, name=f"worker-{i}", daemon=True
            ).start()

        threading.Thread(
            target=self._listener_loop, name="wx-listener", daemon=True
        ).start()

        mode = "LIVE 真发送" if self.s.live else "DRY-RUN 只打印"
        log.info(
            "机器人已启动｜模式：%s｜人设：%s｜监听：%s",
            mode,
            self.persona_name,
            "、".join(self.s.targets()),
        )

        try:
            while not self.stop_flag.is_set():
                if (self.s.data_dir / "STOP").exists():
                    log.warning("检测到 data/STOP，正在退出")
                    break
                finished = getattr(self.io, "finished", None)
                if finished is not None and finished.is_set():
                    log.info("（演示）输入结束，退出")
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C，正在退出")
        finally:
            self.drain(10)
            self.shutdown()

    def drain(self, timeout: float) -> None:
        """退出前把手上正在回的消息处理完，别半句话发一半。"""
        deadline = time.time() + timeout
        while self.q.unfinished_tasks and time.time() < deadline:
            time.sleep(0.2)
        if self.q.unfinished_tasks:
            log.warning("还有 %d 条消息没处理完，直接退出", self.q.unfinished_tasks)

    def _listener_loop(self) -> None:
        # StartListening 在不同版本里可能阻塞也可能立即返回，两种都兼容
        try:
            self.io.start()
        except Exception:
            log.exception("监听线程异常退出")

    def shutdown(self) -> None:
        self.stop_flag.set()
        try:
            if hasattr(self.io, "wx"):
                self.io.wx.StopListening()
        except Exception:
            pass
        log.info("已回复 %d 条消息", self.replied)

    # ---------------- 收消息 ----------------

    def _on_message(self, msg, chat) -> None:
        """这个回调跑在监听线程里，必须足够快，别做重活。"""
        try:
            if not self._accept(msg, chat):
                return
            key = self.io.chat_key(chat, msg)
            self.q.put_nowait((key, msg, chat))
        except queue.Full:
            log.warning("队列满了，丢弃一条消息")
        except Exception:
            log.exception("消息入队异常")

    def _accept(self, msg, chat) -> bool:
        if getattr(msg, "attr", "") != "friend":
            return False  # 自己发的、系统消息都不要
        if getattr(msg, "type", "") != "text":
            log.debug("跳过非文本消息：%s", getattr(msg, "type", "?"))
            return False

        content = (getattr(msg, "content", "") or "").strip()
        if not content:
            return False

        # 群里必须 @ 我
        if self.io.is_group(chat) and self.s.raw["listen"]["at_me_only_in_group"]:
            if self.io.nickname not in content:
                return False

        # 去重
        mid = str(getattr(msg, "id", "") or f"{content}{getattr(msg, 'sender', '')}")
        if mid in self.seen:
            return False
        self.seen[mid] = None
        if len(self.seen) > MAX_SEEN:
            self.seen.popitem(last=False)

        return True

    # ---------------- 工作线程 ----------------

    def _worker(self) -> None:
        while not self.stop_flag.is_set():
            try:
                item = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            key, msg, chat = item
            lock = self.locks.setdefault(key, threading.Lock())
            with lock:
                try:
                    self._handle(key, msg, chat)
                except Exception:
                    log.exception("处理 %s 的消息失败", key)
                finally:
                    self.q.task_done()

    def _handle(self, key: str, msg, chat) -> None:
        content = (getattr(msg, "content", "") or "").strip()
        if self.io.is_group(chat):
            content = content.replace(f"@{self.io.nickname}", "").strip()
        if not content:
            return

        cfg = self.s.humanize

        if humanize.in_quiet_hours(cfg):
            log.info("[静音时段] %s：%s（不回）", key, content)
            return

        if random.random() > float(cfg.get("reply_probability", 1.0)):
            log.info("[随机不回] %s：%s", key, content)
            return

        log.info("← %s：%s", key, content)
        self.memory.append(key, "user", content)

        system = persona.build_system(
            self.persona_text,
            self.persona_name,
            key,
            self.io.is_group(chat),
        )
        messages = [{"role": "system", "content": system}] + self.memory.messages_for(key)

        try:
            raw = self.llm.reply(messages)
        except Exception:
            log.exception("模型调用失败，本轮不回复")
            return

        text = llm_mod.clean(raw, allow_actions=bool(cfg.get("allow_actions")))
        if not text:
            log.warning("模型返回空内容，跳过")
            return

        bubbles = humanize.split_reply(text, cfg)

        # 先"看一眼"，再按字数逐条打字
        time.sleep(humanize.read_delay(cfg))
        for i, b in enumerate(bubbles):
            if i > 0:
                time.sleep(humanize.bubble_gap(cfg))
            time.sleep(humanize.typing_delay(b, cfg))
            if self.s.live:
                self.io.send(chat, b)
            else:
                print(f"[DRY-RUN] 发给 {key}：{b}")

        self.memory.append(key, "assistant", text)
        self.replied += 1
        log.info("→ %s：%s", key, text.replace("\n", " / "))

        if self.memory.needs_summary(key):
            self.memory.summarize(key, self.llm)
