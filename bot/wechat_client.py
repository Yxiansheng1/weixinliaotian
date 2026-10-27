"""微信接入层。真正干活的是 wxauto4（UIAutomation 驱动 4.x 客户端）。

同时提供一个 ConsoleIO，用于不碰微信的本地演示（--demo）。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass

from wxauto4 import WeChat, WxParam

log = logging.getLogger(__name__)


def _pick(d: dict, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k):
            return d[k]
        if isinstance(d, dict):
            for real_k, v in d.items():
                if k in str(real_k) and v:
                    return v
    return None


class WeChatIO:
    """真机接入。"""

    def __init__(self) -> None:
        # 这个库默认会上报遥测，还会把日志写进当前目录，两个都关掉
        WxParam.TELEMETRY_ENABLED = False
        WxParam.ENABLE_FILE_LOGGER = False
        WxParam.LISTEN_INTERVAL = 1

        self.wx = WeChat(ads=False, resize=True)
        info = self.wx.GetMyInfo() or {}
        self.nickname = _pick(info, "nickname", "昵称", "name", "display_name") or "我"
        self.chats: dict = {}
        log.info("已接入微信，当前账号：%s", self.nickname)

    def attach(self, name: str, callback) -> bool:
        # 只借用 AddListenChat 打开该聊天的独立窗口；它的推送回调不可靠
        # （启动时会重放历史消息，且对象不带 id，绕过去重），一律不用。
        try:
            resp = self.wx.AddListenChat(nickname=name, callback=lambda *a, **k: None)
        except Exception:
            log.exception("监听 %s 失败（窗口没打开/昵称不对？）", name)
            return False

        ok = True
        if hasattr(resp, "get"):
            ok = resp.get("status", True) is not False
        if not ok:
            log.error("监听 %s 失败：%s", name, resp)
            return False

        # 拿到这个聊天的独立窗口句柄，后面靠它轮询消息
        try:
            self.chats[name] = self.wx.GetSubWindow(name)
        except Exception:
            log.exception("拿到 %s 的聊天窗口失败", name)
            return False

        log.info("已开始监听：%s", name)
        return True

    def poll_all(self):
        """逐个聊天窗口读全量消息。去重由上层按消息 id 做。"""
        for name, chat in list(self.chats.items()):
            try:
                msgs = chat.GetAllMessage() or []
            except Exception:
                log.exception("读取 %s 的消息失败", name)
                continue
            yield chat, msgs

    def start(self) -> None:
        # 不用库自带的监听线程，消息全靠 poll_all 轮询
        log.info("消息来源：主动轮询（每 %.1f 秒）", WxParam.LISTEN_INTERVAL)

    def send(self, chat, text: str) -> bool:
        try:
            resp = chat.SendMsg(text)
            if hasattr(resp, "get") and resp.get("status") is False:
                log.error("发送失败：%s", resp)
                return False
            return True
        except Exception:
            log.exception("发送异常")
            return False

    def chat_key(self, chat, msg=None) -> str:
        try:
            info = chat.ChatInfo() or {}
        except Exception:
            info = {}
        return (
            _pick(info, "chat_name", "name", "聊天名称")
            or getattr(chat, "who", "")
            or (getattr(msg, "chat_info", "") if msg is not None else "")
            or "unknown"
        )

    def is_group(self, chat) -> bool:
        try:
            t = getattr(chat, "chat_type", "") or ""
        except Exception:
            t = ""
        if isinstance(t, str) and t:
            return "group" in t.lower() or "群" in t
        return False


# ---------------- 演示模式：不碰微信 ----------------

@dataclass
class FakeMsg:
    content: str
    sender: str = "对方"
    attr: str = "friend"
    type: str = "text"
    id: str = ""


class ConsoleIO:
    """在终端里假装你是对方，验证人设、记忆、延迟、拆句是否正常。"""

    def __init__(self, name: str = "测试好友") -> None:
        self.name = name
        self.nickname = "我"
        self._callback = None
        self.finished = threading.Event()

    def attach(self, name: str, callback) -> bool:
        self._callback = callback
        log.info("（演示）已挂载：%s", name)
        return True

    def start(self) -> None:
        def loop():
            print("\n===== 演示模式：直接打字扮演对方，输入 /quit 退出 =====\n")
            try:
                while True:
                    try:
                        line = input("对方 > ").strip()
                    except (EOFError, KeyboardInterrupt):
                        return
                    if not line:
                        continue
                    if line in ("/quit", "/exit"):
                        return
                    self._callback(
                        FakeMsg(content=line, sender=self.name, id=uuid.uuid4().hex),
                        _FakeChat(self.name),
                    )
            finally:
                self.finished.set()

        threading.Thread(target=loop, daemon=True).start()

    def send(self, chat, text: str) -> bool:
        print(f"\n你   > {text}\n")
        return True

    def poll_all(self):
        return []

    def chat_key(self, chat, msg=None) -> str:
        return self.name

    def is_group(self, chat) -> bool:
        return False


class _FakeChat:
    def __init__(self, who: str):
        self.who = who
        self.chat_type = "friend"

    def ChatInfo(self) -> dict:
        return {"chat_name": self.who}

    def SendMsg(self, text: str):
        return {"status": True}
