"""配置加载：config.yaml 打底，缺的字段用默认值补齐。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "mode": "dry_run",          # dry_run = 只打印不发送；live = 真发
    "listen": {
        "contacts": [],         # 白名单：要自动回复的好友昵称（精确匹配）
        "groups": [],           # 群名：只在有人 @ 我 时回复
        "at_me_only_in_group": True,
        "poll_interval": 1.5,   # 轮询新消息的间隔（秒）
    },
    "persona": "personas/default.md",
    "llm": {
        "provider": "relay",
        "base_url": "https://www.tokenav.ai/v1",
        "model": "deepseek-v4-pro",
        "temperature": 1.1,
        "max_tokens": 300,
        "api_key_env": "DEEPSEEK_API_KEY",
        "timeout": 60,
        "penalties": True,
    },
    "humanize": {
        "read_delay": [0.6, 1.8],      # 收到消息后先"看一眼"的时间
        "chars_per_second": 3.2,       # 打字速度
        "min_typing": 0.8,
        "max_typing": 7.0,
        "split_long_reply": True,      # 长回复拆成多条发
        "max_bubble_chars": 55,
        "max_bubbles": 4,              # 一条回复最多拆成几条
        "bubble_gap": [0.7, 1.8],      # 两条消息之间的间隔
        "reply_probability": 1.0,      # <1 表示偶尔"没看见"
        "quiet_hours": [],             # 例如 ["01:00", "07:30"]，期间不回
        "allow_actions": False,        # 是否允许 (括号动作) 这类角色扮演旁白
    },
    "memory": {
        "max_turns": 24,               # 每次带给模型的历史轮数
        "summarize_after": 48,         # 超过多少条就压缩成摘要
    },
    "log_level": "INFO",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Settings:
    raw: dict = field(default_factory=dict)
    root: Path = ROOT
    persona_path: Path = ROOT / "personas" / "default.md"

    # --- 常用快捷访问 ---
    @property
    def mode(self) -> str:
        return str(self.raw.get("mode", "dry_run")).lower()

    @property
    def live(self) -> bool:
        return self.mode == "live"

    @property
    def contacts(self) -> list[str]:
        return list(self.raw["listen"]["contacts"] or [])

    @property
    def groups(self) -> list[str]:
        return list(self.raw["listen"]["groups"] or [])

    @property
    def llm(self) -> dict:
        return self.raw["llm"]

    @property
    def humanize(self) -> dict:
        return self.raw["humanize"]

    @property
    def memory(self) -> dict:
        return self.raw["memory"]

    @property
    def api_key(self) -> str:
        env = self.llm.get("api_key_env", "DEEPSEEK_API_KEY")
        return os.getenv(env, "").strip()

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    def targets(self) -> list[str]:
        return self.contacts + self.groups


def load(config_path: Path | str | None = None) -> Settings:
    load_dotenv(ROOT / ".env")

    path = Path(config_path) if config_path else ROOT / "config.yaml"
    user_cfg: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
    else:
        user_cfg = {}

    merged = _deep_merge(DEFAULTS, user_cfg)

    persona_rel = merged.get("persona", "personas/default.md")
    persona_path = (ROOT / persona_rel).resolve()

    s = Settings(raw=merged, root=ROOT, persona_path=persona_path)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


def validate(s: Settings, require_targets: bool = True) -> list[str]:
    """返回致命问题列表，空列表表示可以启动。"""
    problems: list[str] = []
    if s.mode not in ("dry_run", "live"):
        problems.append(f"mode 只能是 dry_run 或 live，当前是 {s.mode!r}")

    if require_targets and not s.targets():
        problems.append(
            "listen.contacts 和 listen.groups 都是空的。\n"
            "      必须先填至少一个好友昵称——不填白名单就自动回所有人，"
            "在微信里是事故级别的问题。"
        )

    if not s.persona_path.exists():
        problems.append(f"角色卡文件不存在：{s.persona_path}")
    elif len(s.persona_path.read_text(encoding="utf-8").strip()) < 20:
        problems.append(f"角色卡内容太短，像是没写完：{s.persona_path}")

    if not s.api_key:
        env = s.llm.get("api_key_env", "DEEPSEEK_API_KEY")
        problems.append(
            f"没读到 API Key。把 .env.example 复制成 .env，然后在里面填 {env}=sk-..."
        )

    return problems
