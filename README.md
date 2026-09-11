# 守门人 Gatekeeper

> 长篇小说的设定与连续性核对工具。AI 不动笔，只做三件事：从已写章节**抽取**设定与时间线，对新章节**找矛盾**，每条结论**附原文证据**。

调研结论见 `docs/` 与上层目录的《AI 故事创作产品_市场调研报告》：欧美工具、中文网文、短剧编剧、学术基准在同一个地方失败，即长篇一致性；而所有网文平台都允许 AI 做大纲、人设、检查，禁止 AI 代写正文。守门人就切这一刀。

## 在线演示与汇报录屏

| 交付物 | 地址 |
|---|---|
| 线上演示（真实模型通道 + 示例缓存） | https://inner-homework-naturally-policies.trycloudflare.com |
| 汇报录屏（4 分 18 秒） | https://inner-homework-naturally-policies.trycloudflare.com/report.mp4 ／ [Release 附件](https://github.com/1hb6s7t/gatekeeper/releases/download/v1-report/report.mp4) ／ 仓库内 `docs/report.mp4` |
| 仓库 | https://github.com/1hb6s7t/gatekeeper |

演示实例跑在真实模型通道（`GATEKEEPER_PROVIDER=openai`，模型 `mimo-v2.5-pro`），密钥只存在于那台机器的环境变量里。**内置示例这条路径由随仓库分发的缓存直接回放，秒级返回、零额度消耗**，所以评审走下面这五步不需要等模型，作者也不为此付费：

1. 「载入示例」→「切分章节」（内置《青崖夜行》5 章）
2. 「建立设定库」——逐章抽取，15 个实体 / 60 条事实，来源一栏显示「缓存」
3. 「时间线」页看按章事件与「由右手使刀变为左手使刀」这类状态变更链
4. 「新章节」页粘贴第六章（点「载入示例新章」自动填入）→「检查矛盾」→ 9 条发现，每条带设定侧引用、新章侧引用和修改建议
5. 「对照答案卡」——召回 9/10、误报 1、陷阱 0/2、冲突引用逐字命中 9/9

**换自己的稿件**同样能走完整条路径，这时才会真的调用模型：单章抽取 74–80 秒、检查 18–90 秒（实测，取决于章节长度），返回的每条结论照样带逐字引用。额度由 `app/guard.py` 兜底：每访客每小时 20 次、全站每天 150 次、并发 1，超了返回 429 并说明原因；稿件过长返回 413。原稿不需要密钥的离线模式仍然可用，`GATEKEEPER_PROVIDER=cache` 一行即可切回。

汇报录屏 4 分 18 秒，内容是：定位（第 1 节）→ 问题（第 2 节）→ 为什么选这个方向（第 3 节）→ 关键取舍（第 4 节）→ 实机走一遍完整路径（第 5 节，缓存模式实录）→ 三条通道的评测硬数字（第 6 节）→ AI 在研发中如何参与、以及它暴露出的两个自身问题（第 7 节）→ 完成边界与实际投入（第 8 节）。录制用的幻灯片、旁白文本和脚本都在 `docs/report_deck.html`、`docs/narration.json`、`scripts/record_report.py`，可复现。

## 演示实例的密钥与额度边界

演示站可以带真实模型跑，密钥只活在服务端进程的环境变量里：不写进任何文件、不进仓库、不回显，也不以任何形式出现在响应里。前端能拿到的只有通道名和模型 ID，`/api/meta` 就只返回这两个字段。

三层保护都在 `app/guard.py`：

- **密钥只从环境变量读。** 启动脚本 `scripts/serve_public.ps1` 不接受密钥作为参数、不打印它，检测不到就拒绝启动；启动时把通道钉死为 `openai`，因为默认的 `auto` 在上游失败时会回落到 `claude -p`，那等于让陌生访客花掉作者自己的 Claude 额度。
- **错误信息脱敏。** SDK 抛出的异常可能把失败的请求连同请求头一起带出来，而请求头里就是密钥。所有异常文本先过 `guard.scrub()`：只保留第一行、把凭据形状的字符串替换成 `[已隐藏]`、截断到 300 字。服务端日志也过一遍脱敏，因为日志会被贴进 issue 或附件。
- **额度刹车。** 缓存命中完全不碰模型，所以内置示例谁走都免费；其余请求先记账再调用。默认每个访客每 60 分钟 20 次、全站每天 150 次、并发 1（这个网关单章抽取要 78–306 秒，排队比让并发把支出放大更好）。计数按 UTC 日重置并落盘，重启不会发出新额度。

演示站位于 Cloudflare 之后，而 Cloudflare 会把源站的 5xx 换成它自己的错误页，所以失败一律走 4xx，中文说明才到得了评审的浏览器：缓存未命中 `409`，模型调用失败 `424`，额度用尽 `429`，稿件或新章过长 `413`。

自己验一遍：

```bash
python scripts/leak_check.py                            # 本地：探针密钥 + 故意回显 Authorization 的假上游，走完整路径
python scripts/leak_check.py --url https://你的隧道地址   # 线上：只读探测，不消耗额度
```

本地模式先在引擎层确认「上游确实回显了密钥」（否则探针无效），再断言所有响应体、响应头、服务端日志和工作区文件里都没有它。当前结果 PASS。

**这个密钥在聊天里明文出现过，交付前请轮换一个。** 轮换后只需在自己的 shell 里重设 `GATEKEEPER_OPENAI_API_KEY` 再跑启动脚本，仓库、README 和录屏都不用改。

## 托管部署（长效链接）

`cloudflared` 的 quick tunnel 是临时的：进程退出或机器重启就失效，重连还会换域名。要一条长期链接，把仓库接到一个容器托管上即可，Render / Railway / Fly.io / Koyeb / Hugging Face Spaces 都支持从 GitHub 仓库拉取并在 push 后重新部署。仓库里的 `Dockerfile` 就是为此准备的，**默认跑缓存通道，所以托管实例可以完全不需要密钥**：内置示例这条路径由随镜像分发的缓存回放。

```bash
docker build -t gatekeeper .
docker run --rm -p 8080:8080 gatekeeper        # http://localhost:8080
```

实测过：镜像构建通过，容器内零密钥走完示例全路径（5 章 + 检查全部 `来源 cache`、15 实体 60 事实、引用核验 60/60、9 条发现引用逐字命中），`/report.mp4` 可流播，换成访客自己的稿件返回 409 与可读的离线说明。

三个必须先决定或注意的点：

1. **要不要在托管实例上开真实通道。** 开的话密钥要存进那个平台的 secret 环境变量，而且额度对任何拿到链接的人开放（由 `app/guard.py` 兜底）。建议托管实例只跑缓存通道，真实通道留在自己机器上：评审要的是「点开就能走完」，而这条路径本来就不需要模型；真要处理自己的稿件，README 里有两条命令的本地启动方式。
2. **磁盘不是持久的。** 项目、访客缓存和额度计数都落在本地文件。多数免费档的文件系统在重启或重新部署后清空，于是访客项目会消失、当天额度计数会归零（等于削弱每日上限）。要保住就挂持久卷，或者接受它：每个评审本来就会自己新建项目。容器里删掉的那个项目就是这种情况的实证。
3. **只能起一个 worker。** 并发闸门和每访客窗口都在进程内存里，`--workers 2` 会让限制翻倍，还会让落盘的每日计数被并发写坏。`Dockerfile` 的默认命令就是单 worker，改之前先看 `app/guard.py`。

**GitHub 自带的那几样不能用来托管它：** Pages 只发静态文件，而这个项目需要常驻进程响应 `/api/extract`、`/api/check`；Actions 的 runner 是临时的（单个 job 上限 6 小时）且没有对外的入站地址。Codespaces 可以，转发端口就给一个 HTTPS 地址，但它按空闲超时停机、停机期间链接不通、还要消耗免费额度，属于比 quick tunnel 持久、但同样不是常驻。

## 运行

```bash
pip install -r requirements.txt
python -m uvicorn app.server:app --port 8765
# 打开 http://localhost:8765，点「载入示例」→「切分章节」→「建立设定库」
# →「时间线」→「新章节」→「检查矛盾」→「对照答案卡」→「导出 .md」
```

模型通道由环境变量决定：

| 变量 | 取值 | 说明 |
|---|---|---|
| `GATEKEEPER_PROVIDER` | `auto`（默认）/ `openai` / `anthropic` / `claude-cli` / `cache` | `auto` 在有 OpenAI key 时依次尝试 OpenAI 兼容 API、Anthropic SDK、`claude -p`；`cache` 只回放缓存 |
| `GATEKEEPER_MODEL` | 默认 `claude-opus-5` | Anthropic SDK 用的模型 |
| `GATEKEEPER_OPENAI_BASE_URL` | 默认 `https://token-plan-cn.xiaomimimo.com/v1` | OpenAI 兼容 API 根地址 |
| `GATEKEEPER_OPENAI_API_KEY` | 必填（使用 OpenAI 通道时） | 只从环境变量读取，不写入仓库 |
| `GATEKEEPER_OPENAI_MODEL` | 默认 `mimo-v2.5-pro` | OpenAI 兼容 API 的模型 ID；普通 Chat Completions、非流式请求 |
| `GATEKEEPER_CACHE_DIR` | 默认 `data/cache` | 覆盖缓存目录；真实评测建议用 `data/cache_openai`，不要覆盖示例缓存 |
| `GATEKEEPER_CLI_EFFORT` | 默认 `medium` | `claude -p --effort` |
| `GATEKEEPER_CLI_MODEL` | 可选 | `claude -p --model` |
| `GATEKEEPER_CACHE_FALLBACK_DIR` | 默认空 | 只读缓存回退目录。公开演示指向仓库内的 `data/cache`，让内置示例免费回放，同时访客的稿件只写进 `GATEKEEPER_CACHE_DIR`。留空则只读一处：两个缓存里存的是不同模型对同一 prompt 的答案，默认混读会让一次评测看起来像同一个模型的输出 |
| `GATEKEEPER_IP_LIMIT` / `GATEKEEPER_IP_WINDOW_SECONDS` | 默认 `20` / `3600` | 同一访客在窗口内的真实模型调用上限；缓存命中不计 |
| `GATEKEEPER_DAILY_LIMIT` | 默认 `150` | 全站每日真实模型调用上限，按 UTC 日重置 |
| `GATEKEEPER_MAX_CONCURRENCY` | 默认 `1` | 同时在跑的真实模型调用数 |
| `GATEKEEPER_LIMIT_STATE` | 默认 `data/limits.json` | 计数落盘位置（已 gitignore） |
| `GATEKEEPER_DISABLE_LIMITS` | 默认空 | 本地用自己的密钥调试时设为 `1` 关掉刹车 |
| `GATEKEEPER_MAX_TEXT_CHARS` / `_MAX_CHECK_CHARS` / `_MAX_CHAPTERS` | 默认 `400000` / `60000` / `200` | 单次请求的正文、新章与章节数上限，超出返回 `413` |

Anthropic SDK 读标准的 `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`。
OpenAI 通道的四个变量可直接参考仓库里的 `.env.example`；项目不自动加载 `.env`，请在启动进程的 shell 中设置。

## 评估

```bash
set PYTHONIOENCODING=utf-8
set GATEKEEPER_PROVIDER=cache
python eval.py            # 用随仓库分发的缓存，秒级
python tests/smoke_api.py # FastAPI 全路径冒烟，不需要网络

# PowerShell 的真实 OpenAI 评测示例（密钥只在当前 shell 内）
$env:GATEKEEPER_PROVIDER = "openai"
$env:GATEKEEPER_CACHE_DIR = "data/cache_openai"
python eval.py --no-cache
```

内置示例《青崖夜行》：5 章正文 + 预埋 10 处矛盾的第六章 + 2 个陷阱（不应报的地方）。答案卡在 `samples/answer_key.json`。评估输出召回、答案卡外发现数、引用逐字命中率。最近一次结果见下文「当前状态」。

## 唯一必须走通的用户路径

1. 粘贴已写章节，按 `第 N 章`、`Chapter N`、`卷 N`、数字序号或 `===` 分隔线自动切分；超长章节会显示提示
2. 建立设定库：逐章抽取角色、地点、物品、时间线，每条事实带来源章节与逐字引用；引用会与原文比对，未命中的标黄。抽取过程显示秒表，可在当前章节完成后停止并从下一章继续
3. 在「时间线」查看按章事件与「由 X 变为 Y」状态变更链；变更事实可点回原文
4. 粘贴新章，检查：矛盾清单，每条含类型、严重度、设定来源引用、新章冲突引用、一句话建议；点引用可在「原文」页高亮定位
5. 对每条：采纳 / 忽略 / 有意为之。后者会保存结构化规则，可填写备注；下次检查由服务端程序匹配并折叠为「已按规则跳过」
6. 在设定库中手工添加事实，双击事实文本并按回车修改；确认后把新章并入稿件，继续抽取，设定库随连载增长
7. 点击「导出 .md」下载包含设定库、变更、规则和最近一次检查的核对报告

## 哪些是真实的，哪些是缓存，哪些没做

**真实可用**
- 非缓存模式下，切章、抽取、检查、证据校验、规则匹配、时间线解析、并入稿件、手工编辑、Markdown 导出和评估脚本都是真实代码路径
- 三条模型通道：OpenAI 兼容 API（普通非流式 Chat Completions，默认 `mimo-v2.5-pro`）、Anthropic 官方 SDK（流式，`claude-opus-5`）、`claude -p`（Claude Code 非交互模式）
- OpenAI 通道使用独立缓存目录覆盖；输出截断（`finish_reason=length`，该网关带隐藏推理 token）时自动把 `max_tokens` 提到 16000 重试一次，JSON 解析失败时同样只对 OpenAI 通道重试一次并打印最多 300 字诊断片段
- 引用逐字校验在比对前会剥掉两端的「」『』“”等引号标记：mimo 会把每条引用包进引号而正文没有这些符号，严格子串比对会把逐字引用误判成「未核验」
- 引用校验是程序做的，不是模型说的：抽取的 quote 必须逐字出现在该章原文，检查的 conflict_quote 必须逐字出现在新章原文，否则界面标黄
- 「有意为之」的规则过滤是服务端程序做的，依据实体名、事实文本和引用的可解释文本匹配，不依赖模型自觉
- `python tests/smoke_api.py` 会在 `GATEKEEPER_PROVIDER=cache` 下覆盖载入示例、5 章抽取、检查、规则、变更识别、手工事实编辑、并入稿件和导出
- `python scripts/ui_smoke.py` 用 Playwright 在真实浏览器里点完整路径：停止/继续抽取、检查、答案卡面板、规则创建→重检跳过→删除、手工添加与行内编辑、导出下载头；跑在缓存模式，秒级，需要本地服务在 :8765

**缓存**
- `data/cache/` 里存有内置示例 5 章抽取 + 第六章检查的模型原始输出，按 prompt 哈希命中。界面「来源」会显示「缓存」。取消勾选「允许使用缓存结果」即可强制重新调用
- `GATEKEEPER_CACHE_DIR=data/cache_openai` 可把真实评测产生的原始输出隔离到另一目录；`data/cache_*/` 被 git 忽略，不会覆盖随仓库分发的示例缓存
- 缓存只覆盖内置示例；粘贴自己的稿件一定走真实调用。相同项目、相同章节的重复检查在规则变化时可以复用项目内上一次发现，再重新执行程序级规则匹配

**没做**
- 没有账号、数据库、多用户；项目存为 `data/projects/*.json`
- 没有对 30 万字以上稿件的分段策略；设定库整体随 prompt 传入，几十章后会撞上下文与费用
- 没有「采纳建议」后自动改写新章，采纳只是标记状态
- 短剧剧本模式（按集诊断冲突、爽点、集末钩子）没做，只在 PRD 里作为第二种文档类型
- 规则匹配是实体名 + 文本/引用的启发式，不是语义级同义改写判断；删除规则后会恢复关联发现
- 时间线依赖模型在事实中输出「第 N 章」或当前章节可推断的前缀；不会自动校正故事世界观日期
- 导出只包含最近一次检查，不保留检查历史快照；停止只能发生在章节请求之间，不能取消已经发出的单次模型请求
- 没有用官方 Anthropic 端点实测过 SDK 通道（见「当前状态」）
- OpenAI 通道的抽取引用 3/52 未逐字命中（mimo 改写而非格式差异）；检查引用 1/9 因「`「A」和「B」`」双引号拼接格式无法通过单子串校验，需要按「和」拆开后分别比对

## 当前状态

当前已验证的证据分层如下：

| 层级 | 结果 |
|---|---|
| 离线缓存回归（2026-09-10） | 15 个实体 / 60 条事实；引用 60/60；第六章 9 条发现；召回 9/10；误报 1；陷阱 0/2；冲突引用 9/9；秒级 |
| 全路径 API 冒烟（2026-09-10） | `tests/smoke_api.py`：PASS；覆盖规则、规则重检、变更识别、手工添加/编辑、并入稿件、导出 |
| 浏览器级 UI 冒烟（2026-09-10） | `scripts/ui_smoke.py`：PASS；真实点击停止/继续抽取（停在第 1 章、续跑到 5 章）、检查、答案卡面板、规则创建→重检跳过→删除、手工添加与行内编辑、导出下载头；无 console 错误 |
| 真实 Claude CLI（2026-09-09） | `claude -p`、`--effort medium`、模型 `claude-opus-5`：15/60，召回 9/10，误报 1，陷阱 0，冲突引用 9/9，检查 86 秒 |
| 真实 OpenAI 兼容 API（2026-09-10） | `mimo-v2.5-pro`：15 实体 / 52 事实，抽取引用 49/52，检查引用 8/9（剥引号后）；召回 9/10，误报 0，陷阱 0；每章抽取 78–306 秒，检查 90 秒；第一章输出截断一次，`max_tokens` 提到 16000 重试成功。完整日志 `data/eval_openai.log`，原始输出在 `data/cache_openai/`（git 忽略） |

对照 PRD 成功标准：缓存回归的召回 ≥7、误报 ≤3、离线走通均达标；「一次检查 60 秒内」在两个真实通道下都**没有达标**（claude -p 86 秒、mimo 90 秒），缓存时秒级。OpenAI 通道实测召回 9/10、误报 0、陷阱 0，准确率达标；耗时瓶颈在网关推理 token 与逐章调用，未做并行或分片。

已知环境限制：当前 `ANTHROPIC_BASE_URL` 指向的中转站拒绝 Anthropic SDK 的所有请求（400/503），所以历史 `auto` 运行实际退到 `claude -p`。用户提供的 DeepSeek/兼容网关若把流式响应错误地返回给非流式重试，Claude Code 会出现 HTTP 200 + `content-type: event-stream` 的解析错误；Gatekeeper 的 OpenAI 通道显式使用非流式请求，但仍需用真实 key 验证该网关的端到端稳定性。

界面截图：`docs/shot1_chapters.png`（切章）、`shot2_bible.png`（设定库）、`shot3_findings.png`（矛盾清单）、`shot4_reader.png`（引用高亮）、`shot5_eval.png`（答案卡）、`shot6_timeline.png`（时间线/变更链）、`shot7_rules.png`（规则折叠区）。前五张为此前真实 UI 走通证据，后两张对应本次功能；截图不替代 API 与真实模型证据。

## 下一步优先级

1. 引用校验的残余缺口：检查侧 1/9 是「`「A」和「B」`」双引号拼接格式，按「和」拆开后分别比对可补上；抽取侧 3 条是模型真改写，可考虑在 CHECK/EXTRACT prompt 里加一句「引用不加任何引号、必须逐字」并重新生成 mimo 通道缓存（改动会使 `data/cache/` 示例缓存失效，需一并重生成）
2. 误报与召回：用更多人工标注样本评估规则匹配边界，完善语义变化与视角限制的人工确认流
3. 规模：设定库按实体检索注入而不是整体注入；对 50 章以上稿件分卷；真实通道每章 78–306 秒，可先并行化逐章抽取
4. 剧本模式：按集拆分，检查角色出场连续性与卡点结构

## 目录

```
app/engine.py   切章、prompt、三条模型通道、缓存、合并、校验、变更识别
app/guard.py    公开演示的额度刹车与错误脱敏（密钥不经手、只被读取）
app/server.py   FastAPI 路由与 JSON 文件存储
static/index.html  单页前端（稿件 / 设定库 / 时间线 / 矛盾清单 三栏）
samples/        示例稿件、第六章、答案卡
eval.py         评估脚本
tests/smoke_api.py  缓存模式全路径 API 冒烟
scripts/serve_public.ps1        带真实密钥启动公开演示（密钥只从环境变量读）
scripts/leak_check.py           密钥泄漏探针：本地全路径 + 恶意上游回显 / 线上只读
scripts/shot_timeline_rules.py   Playwright 截时间线/规则两张界面图
scripts/ui_smoke.py    Playwright 浏览器级全路径冒烟
scripts/tts_narration.ps1       用 Windows SAPI 从 docs/narration.json 生成 8 段旁白
scripts/record_report.py        Playwright 录屏：幻灯片按旁白时长配速 + 实机走一遍缓存路径
scripts/build_report_video.py   拼接旁白并 mux 到录屏，产出 docs/report.mp4
.env.example    OpenAI 兼容通道配置占位
docs/PRD.md     一页 PRD；IMPLEMENTATION_PLAN_4H.md 为本次实施方案
docs/report_deck.html + narration.json + report.mp4   汇报幻灯片、旁白文本、成片
```
