# 微信陪聊机器人

用你自己的微信账号，接一个大模型，让它以某个角色的身份跟指定的人聊天。

- 监听指定好友（或群里 @ 你）的新消息
- 用角色卡控制人设：性格、说话方式、口头禅、禁则
- 有长期记忆：按人分别存，聊多了自动压缩成摘要，不会聊一个月就失忆
- 拟人化：先"看一眼"再回、按字数算打字时间、长回复拆成几条、深夜不回
- 默认 `dry_run`：只打印它想发什么，不发出去。确认满意再切 live

---

## 微信版本：必须 4.1.8，再高就读不到界面

**这是本项目最容易踩死的一步，先看完再装。**

微信从 4.1.9 开始把主界面改成 GPU 画布自绘，只向 Windows 辅助功能上报一个窗口外壳。
消息区、输入框、会话列表**一个控件节点都没有**，任何基于 UIAutomation 的库
（wxauto4、pywinauto、FlaUI）都读不到消息，和配置无关。

本机实测三个版本，同一台机器、同一套代码：

| 微信版本 | 登录窗口控件树 | 主窗口控件树 | 能否用 |
|---|---|---|---|
| **4.1.8.107** | 28 个节点 | **65 个节点（`mmui::MainWindow`）** | ✅ 可用 |
| 4.1.9.57 | 26 个节点 | 2 个空壳 | ❌ 不可用 |
| 4.1.15.56 | 2 个空壳 | 2 个空壳 | ❌ 不可用 |

排查时确认过的其他事实（免得再试一遍）：

- 主窗口在 Win32 层面是存在的（`Qt51514QWindowIcon`，标题「微信」），不是找不到窗口
- 切到前台、`SetFocus`、开 Windows 讲述人、走 MSAA `AccessibleObjectFromWindow`，
  都不能让 4.1.9+ 的控件树长出来
- 子窗口 `MMUIRenderSubWindowHW` 的 UIA 树也是 0 个节点

所以 wxauto4 文档写「适用最新 4.1.8」是准确的，不要相信「4.1.9 也能用」的说法。

### 降级与锁版本

1. 退出微信，先备份 `%USERPROFILE%\Documents\xwechat_files`（或你自定义过的数据目录）
2. 官方 CDN 只留最新版，旧安装包要用 [web.archive.org](https://web.archive.org) 存快照。
   取到的文件必须做两道验证，都过才能装：
   - MD5 对上档案里 `x-archive-orig-x-cos-meta-md5` 头的值（这是腾讯 COS 的原始值）
   - `Get-AuthenticodeSignature` 状态为 `Valid`，签发者 `Tencent Technology (Shenzhen) Company Limited`
3. 装完立刻堵更新，三件事缺一不可：
   - 删掉 `%APPDATA%\Tencent\xwechat\update\download` 里已下好的更新包
     （**最容易漏**：包一旦下好，光改更新程序名没用，下次启动会被直接应用）
   - 把 `WeixinUpdate.exe` 删掉，用**同名目录**占住这个名字
   - `icacls` 拒绝当前用户对 `%APPDATA%\Tencent\xwechat\update` 的写权限

不锁版本的话，微信会在登录后自动升回最新版，之前所有工作白做。

---

## 先说清楚风险

**这是个人微信自动化，不是官方接口。**

- 违反《微信个人账号使用规范》，轻则限制登录，重则封号
- 腾讯有行为检测：秒回、固定句式、7×24 在线、大量重复内容，都是特征
- 这个项目做了拟人化，但**不能保证不被检测**

所以：

1. **用小号测**，别拿你的主力号（绑着工作、家人、支付的号）第一个上
2. 先只挂 1 个好友，跑两天看有没有异常（异常登录提示、消息发送失败）
3. 别群发、别做营销、别自动加人
4. 别让它冒充真实存在的人去骗人——这条是法律问题，不是技术问题

要合规又想对外用，应该走企业微信或微信客服的官方接口，那是另一套东西。

---

## 快速开始

### 1. 装依赖

```powershell
cd wechat-bot
.\.venv\Scripts\activate
pip install -r requirements.txt
```

（`.venv` 已经建好了。换台机器就 `python -m venv .venv` 重来一遍。）

### 2. 填 API Key

```powershell
copy .env.example .env
notepad .env
```

现在配的是中转站 `tokenav`（`https://www.tokenav.ai/v1`），key 就用它后台生成的那个，填进 `DEEPSEEK_API_KEY`。

换了中转站或直连官方，改 `config.yaml` 里三行就行：

```yaml
llm:
  base_url: https://www.tokenav.ai/v1
  model: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY
```

不确定中转站给的模型名是什么，跑这条查：

```powershell
python probe.py --models
```

它会把接口里所有可用模型列出来，并标出当前配置用的那个。把 `model` 改成列表里的名字再重跑。

中转站报 `400` 且提示参数不支持，就把 `llm.penalties` 设成 `false`——有些中转不接受 `presence_penalty` / `frequency_penalty`。

### 3. 填白名单

打开 `config.yaml`，把 `listen.contacts` 改成你要自动回复的人：

```yaml
listen:
  contacts:
    - 张三
```

**昵称必须和微信里显示的一字不差。** 改了备注就用备注。

### 4. 自检

```powershell
python probe.py
```

会依次检查 Python、wxauto4、配置、角色卡、微信窗口、模型连通性。
哪一项红了，按提示修。

> 连微信这一步要求**微信主窗口是打开的**（登录状态下点右下角托盘图标，把聊天窗口调出来）。

### 5. 先在终端里试人设

```powershell
python run.py --demo
```

不碰微信。你在终端里扮演对方打字，看它回得像不像人。
觉得人设不对就改 `personas/default.md`，改完重跑，几轮就能调到位。

### 6. dry-run

```powershell
python run.py
```

连上微信，真正监听，但只把要发的内容打印出来（`[DRY-RUN] 发给 张三：...`）。

### 7. 切 live

`config.yaml` 里改成 `mode: live`，重跑。这时候才会真的发。

停止：`Ctrl+C`，或者在项目里建个 `data/STOP` 文件。

---

## 多用户记忆管理

每个聊天对象一份独立记忆，文件在 `data/memory/<昵称>.json`，
互不影响：清空一个人不会动到别人，改一个人的人设也不会串。

### 配置里怎么用

```yaml
listen:
  contacts:          # 同时监听多个人
    - 张三
    - 李四

persona: personas/default.md   # 没单独指定的人用这个

persona_map:                   # 给特定的人换人设
  李四: personas/lover.md
```

### 命令行怎么管

```powershell
python memory_cli.py list                     # 谁有记忆、聊了多少、最近什么时候
python memory_cli.py stats                    # 总量：人数、消息数、占多少磁盘
python memory_cli.py show 张三 --limit 20     # 看某个人最近聊了什么
python memory_cli.py summary 张三             # 只看摘要
python memory_cli.py summarize 张三 --force   # 立刻压缩（不等阈值）
python memory_cli.py export 张三 --out 张三.md
python memory_cli.py persona 张三 personas/lover.md   # 单独给这个人换角色卡
python memory_cli.py persona 张三 -           # 取消，恢复默认
python memory_cli.py rename 旧昵称 新昵称      # 微信改名后迁移记忆
python memory_cli.py clear 张三 --yes         # 清空对话，保留档案
python memory_cli.py delete 张三 --yes        # 整个删掉
```

只读命令（`list` / `stats` / `show` / `summary` / `export`）机器人跑着也能用。
`clear` / `delete` / `rename` 会改文件，建议先停机器人。

### 记忆里存了什么

```json
{
  "chat": "张三",
  "summary": "结构化笔记：关系、对方的近况、没说完的事、情绪、称呼梗",
  "messages": [{"role": "user", "content": "…", "ts": 1761…}],
  "meta": {"first_seen": 1761…, "last_seen": 1761…, "persona": "personas/lover.md"}
}
```

- 摘要不是流水账，是能用来"接着聊"的笔记，压缩时按这个目标生成
- `meta.persona` 是给这个人单独指定的人设，优先于 `config.yaml` 里的 `persona_map`
- 消息数超过 `memory.summarize_after` 自动压缩一半，旧的在摘要里，新的留原文

### 隐私提醒

`data/memory/` 里是**真实聊天内容**，已经在 `.gitignore` 里排除，别提交、别外传。
要给别人看效果，用 `export` 导一份，自己检查过再发。

---

## 目录结构

```
wechat-bot/
├── run.py               启动入口
├── probe.py             开跑前自检（--models 查可用模型）
├── memory_cli.py        多用户记忆管理（列表/查看/导出/清理/按人设）
├── bench_models.py      换模型时对比谁更像人
├── selftest.py          纯逻辑自测，不联网
├── config.yaml          所有配置
├── .env                 API Key（自己建，别提交）
├── bot/
│   ├── config.py        配置加载与校验
│   ├── engine.py        调度核心：收消息 → 判断 → 调模型 → 发送
│   ├── wechat_client.py 微信接入（wxauto4 封装）+ 终端演示模式
│   ├── llm.py           模型调用 + 输出清洗
│   ├── memory.py        按人存记忆 + 定期摘要
│   ├── persona.py       角色卡加载 + system prompt 组装
│   ├── humanize.py      延迟、拆句、静音时段
│   └── log.py           日志
├── personas/
│   ├── default.md       角色卡：老朋友「阿澈」
│   └── lover.md         角色卡：恋人「小满」
└── data/
    ├── memory/*.json    每个聊天对象的记忆
    └── logs/bot.log     运行日志
```

---

## 人设怎么写

角色卡就是纯 markdown，不用会代码。四个部分最有用：

| 部分 | 作用 | 写不好会怎样 |
|---|---|---|
| 你是谁 | 年龄、职业、和对方的关系、生活细节 | 模型会很虚，说话没根 |
| 性格 | 3–5 条，互相有点张力 | 写成"温柔善良"等于没写 |
| 说话方式 | 句子长度、常用词、不用的词 | 最容易崩，一定要具体 |
| 说话示例 | 3 组「对方说 → 你回」 | **最有效的一节**，比形容词管用十倍 |

写完 `persona: personas/你的文件.md` 指过去就行。

调人设的循环：`run.py --demo` → 挑毛病 → 改角色卡 → 再来。
一般改三轮就能看出味道。

---

## 几个关键参数

```yaml
humanize:
  chars_per_second: 3.2   # 打字速度。调到 2.0 会明显慢，像在认真想
  reply_probability: 0.9  # 有一成装没看见，反而更像真人
  quiet_hours: ["01:00", "07:30"]   # 这个时间段不回，第二天早上再理
  split_long_reply: true  # 长回复拆成几条，避免"一大段工整文字"
```

```yaml
memory:
  max_turns: 24       # 每次带多少轮历史，越大越连贯但越贵
  summarize_after: 48 # 超过就压缩，防止上下文越滚越大
```

模型温度建议 1.0–1.2。太低会像客服，太高会开始胡说。

---

## 常见问题

**连不上微信 / "未找到已登录的客户端主窗口"**
微信主窗口没开。点托盘图标把窗口调出来，再跑一次。

**监听上了但没反应**
- 昵称不对（差一个字、用了备注外的名字）
- 消息不是文本（图片、语音、表情包目前不回）
- 群聊里没 @ 你

**发了但发错人**
白名单漏填了。先回去把 `contacts` 补全，别用"回所有人"这种想法。

**运行中不要手动操作同一个聊天窗口**
程序要抢焦点去输入，你在旁边点会互相打断。要么让它跑，要么先停。

**微信更新后失效**
`wxauto4` 依赖客户端的 UI 结构。微信大版本更新大概率要等库更新。
建议关掉微信自动更新，或者更新前先备份这个环境。

**中转站报错 / 模型名不对**
先 `python probe.py --models` 把可用模型列出来对照。常见错的三种：
- `model` 写的是官方名（如 `deepseek-chat`），中转那边叫 `deepseek-v3` 之类
- `base_url` 少了 `/v1`
- 报 400 说参数不支持 → `llm.penalties: false`

**它说话越来越像 AI**
1. 调低 `max_tokens`（300 → 200）
2. 把摘要压缩开小一点，`summarize_after: 30`
3. 角色卡里"说话示例"再加两组，比什么都管用

---

## 可以往上加的东西

- 发图片 / 表情包（`wx.SendFiles` 已经支持，接一下就行）
- 语音消息转文字（需要本地 ASR）
- 微信群里的多角色互动
- 一个简单的本地网页看板，看它今天聊了什么
- 把记忆换成向量检索，适合长期陪伴

需要哪个跟我说，我接着写。
