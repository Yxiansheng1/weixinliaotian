"""换模型时用这个对比：同一个角色卡、同一段对话，看谁更像人。

    python bench_models.py                        # 测接口下所有模型
    python bench_models.py deepseek-v4-pro         # 只测指定模型
"""

from __future__ import annotations

import argparse
import sys
import time

from bot import config, persona
from bot.llm import ChatLLM, clean

TURNS = [
    "在干嘛",
    "今天加班到十点",
    "有点累",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", help="不指定就测接口下全部模型")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    s = config.load(args.config)
    if not s.api_key:
        print("没读到 API Key，先建 .env")
        return 1

    persona_text = persona.load_persona(s.persona_path)
    name = persona.persona_name(persona_text, s.persona_path.stem)
    system = persona.build_system(persona_text, name, "张三", False)

    models = args.models
    if not models:
        try:
            models = ChatLLM(s.llm, s.api_key).list_models()
        except Exception as e:
            print(f"拉不到模型列表：{e}")
            return 1

    print(f"角色卡：{name}｜对比 {len(models)} 个模型\n")

    for m in models:
        cfg = dict(s.llm)
        cfg["model"] = m
        cli = ChatLLM(cfg, s.api_key)
        print("=" * 58)
        print(f"模型：{m}")
        total = 0.0
        history: list[dict] = [{"role": "system", "content": system}]
        for t in TURNS:
            history.append({"role": "user", "content": t})
            t0 = time.time()
            try:
                raw = cli.reply(history)
            except Exception as e:
                print(f"  失败：{type(e).__name__}: {str(e)[:140]}")
                break
            dt = time.time() - t0
            total += dt
            txt = clean(raw, allow_actions=False)
            history.append({"role": "assistant", "content": txt})
            print(f"  对方：{t}")
            for line in txt.splitlines():
                print(f"  回复：{line}")
            print(f"        [{dt:.1f}s，{len(txt)} 字，{len(txt.splitlines())} 条]")
        else:
            print(f"  合计 {total:.1f}s")

    print("\n选好之后把 config.yaml 的 llm.model 改掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
