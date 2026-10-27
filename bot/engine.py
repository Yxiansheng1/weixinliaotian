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
        self.seen_lock = threading.Lock()
        # 每个聊天一个游标：(已处理条数, 最后一条的指纹)
        # 库给出的消息 id 在两次读取之间会变，不能用来去重，只能按位置判定
        self.cursors: dict[str, tuple] = {}
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

        # 把启动前就存在的历史消息记为"已读"，否则一上线就会把旧消息全回一遍
        self.prime()

        threading.Thread(
            target=self._listener_loop, name="wx-listener", daemon=True
        ).start()

        threading.Thread(target=self._poller, name="wx-poller", daemon=True).start()

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

    def _fp(self, m) -> tuple:
        """消息指纹：用来确认"上次读到的那条"还在不在同一位置。"""
        return (
            getattr(m, "attr", ""),
            getattr(m, "sender", ""),
            (getattr(m, "content", "") or "")[:60],
        )

    def prime(self) -> None:
        """记下每个聊天当前读到的位置。启动前的历史一条都不回。"""
        total = 0
        try:
            for chat, msgs in self.io.poll_all():
                msgs = list(msgs)
                key = self.io.chat_key(chat)
                self.cursors[key] = (len(msgs), self._fp(msgs[-1]) if msgs else None)
                total += len(msgs)
        except Exception:
            log.exception("初始化历史消息失败，稍后轮询会自动补上")
        log.info("已跳过启动前的 %d 条历史消息", total)

    def _new_messages(self, key: str, msgs: list) -> list:
        """按游标挑出这次的新消息。id 不可靠，只认位置。"""
        fps = [self._fp(m) for m in msgs]
        n, last_fp = self.cursors.get(key, (0, None))
        start = 0

        if last_fp is None:
            start = 0
        elif n <= len(msgs) and n > 0 and fps[n - 1] == last_fp:
            start = n
        else:
            # 窗口里的消息被裁剪或重排过，从后往前找上次那条的位置
            start = None
            for i in range(len(fps) - 1, -1, -1):
                if fps[i] == last_fp:
                    start = i + 1
                    break
            if start is None:
                log.warning("%s 的消息列表对不上，本轮不处理（避免重复回复）", key)
                start = len(msgs)

        self.cursors[key] = (len(msgs), fps[-1] if fps else None)
        return msgs[start:]

    def _poller(self) -> None:
        """主动轮询每个聊天窗口的新消息。

        不用库的推送回调——实测那个回调在本机不触发，
        而且库返回的消息 id 每次读取都会变，所以按"读到第几条"判定新消息。
        """
        interval = float(self.s.raw["listen"].get("poll_interval", 1.5))
        while not self.stop_flag.is_set():
            try:
                for chat, msgs in self.io.poll_all():
                    key = self.io.chat_key(chat)
                    for m in self._new_messages(key, list(msgs)):
                        self._on_message(m, chat)
            except Exception:
                log.exception("轮询消息时出错")
            time.sleep(interval)

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
            log.info("收到新消息｜%s｜id=%s｜来源=%s", key, getattr(msg, "id", "?"), threading.current_thread().name)
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

        # 去重：轮询和推送可能同时送来同一条，靠 id 拦住
        mid = str(getattr(msg, "id", "") or f"{content}{getattr(msg, 'sender', '')}")
        with self.seen_lock:
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
