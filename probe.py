"""开跑之前先自检：环境、配置、微信窗口、模型连通性。

    python probe.py
    python probe.py --skip-llm     # 不想花 token 就跳过模型测试
"""

from __future__ import annotations

import argparse
import sys
import traceback

from bot import config
from bot import log as logmod
from bot.llm import ChatLLM

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    logmod.setup("WARNING")
    s = config.load(args.config)
    fails = 0

    def report(ok: bool, title: str, detail: str = "", warn_only: bool = False):
        nonlocal fails
        tag = OK if ok else (WARN if warn_only else BAD)
        if not ok and not warn_only:
            fails += 1
        print(f"{tag} {title}")
        if detail:
            for line in str(detail).splitlines():
                print(f"       {line}")

    # 1. Python
    report(
        sys.version_info >= (3, 9),
        f"Python {sys.version.split()[0]}",
        "需要 3.9 以上" if sys.version_info < (3, 9) else "",
    )

    # 2. wxauto4
    try:
        import wxauto4
        from wxauto4.param import VERSION

        report(True, f"wxauto4 {VERSION} 已安装")
    except Exception as e:
        report(False, "wxauto4 未正确安装", f"{e}\n运行：pip install -r requirements.txt")

    # 3. 配置
    problems = config.validate(s)
    report(not problems, "配置检查", "\n".join(problems))

    # 4. 角色卡
    if s.persona_path.exists():
        report(True, f"角色卡：{s.persona_path.name}", f"{len(s.persona_path.read_text(encoding='utf-8'))} 字")
    else:
        report(False, "角色卡缺失", str(s.persona_path))

    # 5. 微信窗口
    wx = None
    try:
        from wxauto4 import WeChat, WxParam

        WxParam.TELEMETRY_ENABLED = False
        wx = WeChat(ads=False, resize=False)
        info = wx.GetMyInfo() or {}
        name = info.get("nickname") or info.get("昵称") or "?"
        report(True, f"已连接微信客户端（{name}）")

        ss = wx.GetSession() or []
        names = [getattr(x, "name", "") for x in ss][:10]
        print(f"       当前会话前 {len(names)} 个：{'、'.join(names) if names else '（空）'}")

        targets = s.targets()
        hit = [t for t in targets if t in names]
        if targets:
            report(
                len(hit) == len(targets),
                "白名单昵称能在会话列表里对上",
                f"没找到：{'、'.join(set(targets) - set(hit))}\n"
                "（可能只是最近没聊过，不影响监听，但昵称必须和微信里完全一致）",
                warn_only=True,
            )
    except Exception as e:
        report(
            False,
            "连接微信失败",
            f"{e}\n"
            "1. 确认微信已登录\n"
            "2. 把微信主窗口点出来（托盘图标 → 打开聊天窗口）\n"
            "3. 再跑一次 probe.py",
        )

    # 6. 模型
    if args.skip_llm:
        print(f"{WARN} 已跳过模型连通性测试")
    elif not s.api_key:
        report(False, "未配置 API Key", f"在 .env 里填 {s.llm.get('api_key_env')}=sk-...")
    else:
        try:
            client = ChatLLM(s.llm, s.api_key)
            out = client.reply(
                [{"role": "user", "content": "只回复两个字：收到"}]
            )
            report(True, f"模型可用（{s.llm.get('model')}）", f"返回：{out[:40]}")
        except Exception as e:
            report(False, "模型调用失败", f"{type(e).__name__}: {e}")

    print()
    if fails:
        print(f"有 {fails} 项没过，先修完再启动。")
        return 1

    print("全部通过。下一步：")
    print("  python run.py --demo     # 先不碰微信，验证人设好不好用")
    print("  python run.py            # dry-run，能看到它想发什么但不真发")
    print("  把 config.yaml 的 mode 改成 live 后，才是真的会发消息")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
