# 守门人 Gatekeeper · 4 小时功能完善实施方案（交 DeepSeek 执行）

## Context

- 项目：`C:\Users\shenh\gatekeeper`，求职实操题「AI 故事创作产品」的交付物。已完成一条完整用户路径（切章 → 建立设定库 → 新章查矛盾 → 采纳/忽略/有意为之 → 并入稿件），已首次提交（977049a）。
- 现状：FastAPI（`app/server.py` 188 行）+ 引擎（`app/engine.py` 252 行）+ 单页前端（`static/index.html` 191 行）+ JSON 文件存储 + prompt 哈希缓存（`data/cache/`）。内置示例《青崖夜行》真实评测：召回 9/10、误报 1、陷阱 0、引用逐字命中 9/9。
- 问题：(1) 当前只能走 `claude -p`（中转站拒绝 Anthropic SDK），每章 1–4 分钟，无进度、不可中断；(2)「有意为之」只是把一段文本塞进 prompt 让模型自觉不报，不可靠、不可见；(3) 设定库只读，抽错了只能删；(4)「由 X 变为 Y」的状态变更淹没在普通事实里；(5) 切章只认「第N章」；(6) 没有导出。
- 目标：按用户选择「深挖核心路径」，用 4 小时把这条路径做扎实，并接入用户提供的 OpenAI 兼容端点做真实调用。不做剧本模式、不做账号/数据库、不做正文生成。

## 给实施方的硬约束（先读）

1. **不要改动 `EXTRACT_PROMPT`、`CHECK_PROMPT`、`SYSTEM`、`compact_bible()` 的输出格式**。缓存按 `kind + prompt` 的 sha256 命中（`engine._cache_key`），改一个字，`data/cache/` 里随仓库分发的 6 个示例缓存全部失效，离线演示就断了。若确需改 prompt，放到最后一步统一改，并重新生成缓存（见 T7）。
2. 密钥只从环境变量读，**不写进任何文件、不提交**。用户提供的端点：`https://token-plan-cn.xiaomimimo.com/v1`，可用模型 `mimo-v2.5-pro`（推荐默认）、`mimo-v2.5`。密钥由用户在 shell 里设置 `GATEKEEPER_OPENAI_API_KEY`。仓库里只加 `.env.example`（占位符）。
3. 已实测：该端点两个模型都带隐藏推理 token；`response_format={"type":"json_object"}` 在 `mimo-v2.5` 上会截断输出。**不要用 json 模式**，用普通模式 + 现有 `engine.parse_json()`（已能剥 ```json 围栏与尾部杂质），`max_tokens` 给 8000。
4. 保持单文件前端（vanilla JS，无框架、无构建）、单文件引擎；不新增 Python 依赖（`openai` 2.41 已装，加进 `requirements.txt` 即可）。
5. Windows 环境：Python 3.13，所有文件读写显式 `encoding="utf-8"`；终端打印中文前 `set PYTHONIOENCODING=utf-8`。
6. 每完成一个任务跑一次 `python eval.py`（缓存，1 秒内）+ `python tests/smoke_api.py`（T0 建立），通过后 `git commit`。任一任务超出时间盒 30% 就砍掉它的「可选」部分，往下走。
7. 交付前必须更新 README 的「哪些是真实的 / 缓存 / 没做」与「当前状态」，如实写。

## 任务清单（合计 240 分钟）

| # | 任务 | 时间盒 | 主要文件 |
|---|---|---|---|
| T0 | 基线与冒烟脚本 | 10 | `tests/smoke_api.py` |
| T1 | OpenAI 兼容模型通道 | 35 | `app/engine.py` `app/server.py` `README.md` `.env.example` |
| T2 | 长任务进度、计时、可中断 | 20 | `static/index.html` |
| T3 | 结构化「有意为之」规则与程序级过滤 | 45 | `app/server.py` `app/engine.py` `static/index.html` |
| T4 | 状态变更标记与时间线视图 | 40 | `app/engine.py` `static/index.html` |
| T5 | 切章容错 + 设定库手工增改 | 30 | `app/engine.py` `app/server.py` `static/index.html` |
| T6 | 导出核对报告（Markdown） | 25 | `app/server.py` `static/index.html` |
| T7 | 真实评测、README、截图、提交 | 35 | `README.md` `docs/` |

---

### T0 · 基线与冒烟脚本（10 分钟）

1. `pip install -r requirements.txt`；`python eval.py` 应输出「召回 9/10，误报 1，踩陷阱 0」「冲突引用逐字命中 9/9」，来源全部 `[cache]`。
2. 新建 `tests/smoke_api.py`：用 `fastapi.testclient.TestClient` 在 `GATEKEEPER_PROVIDER=cache` 下走完整路径并断言（这段逻辑上一会话已验证可用，直接照写）：
   - `GET /api/sample` → `POST /api/projects` 得 5 章 → 5 次 `POST /extract` 全部 `source=="cache"` → `POST /check`（`title=sample.new_chapter_title, text=sample.new_chapter`，注意 text 带标题行，服务端会用 `split_chapters` 剥掉）得 9 条、`conflict_verified` 全真 → 对第一条 `POST .../findings/{fid}` `{"status":"intentional"}` 后 `project["intentional"]` 长度 1 → `POST .../commit` 后 `chapters` 长度 6。
   - 末尾删除生成的 `data/projects/{pid}.json`。
   - 后续任务给它追加断言。
3. 提交：`test: smoke script for the full user path`。

### T1 · OpenAI 兼容模型通道（35 分钟）

**engine.py**
- 新增 `_call_openai(prompt, system) -> str`：
  ```python
  from openai import OpenAI
  client = OpenAI(base_url=os.environ["GATEKEEPER_OPENAI_BASE_URL"], api_key=os.environ["GATEKEEPER_OPENAI_API_KEY"], timeout=600)
  r = client.chat.completions.create(model=OPENAI_MODEL, messages=[{"role":"system","content":system},{"role":"user","content":prompt}], max_tokens=8000, temperature=0)
  ```
  返回 `r.choices[0].message.content`；`finish_reason=="length"` 时抛 `RuntimeError("输出被截断")`。
- 模块级 `OPENAI_MODEL = os.environ.get("GATEKEEPER_OPENAI_MODEL", "mimo-v2.5-pro")`。
- `PROVIDER` 合法值加 `openai`；`call_model()` 的 `auto` 顺序改为：`openai`（仅当 `GATEKEEPER_OPENAI_API_KEY` 已设）→ `anthropic` → `claude-cli`。分发处把 if/else 改成 dict `{"openai": _call_openai, "anthropic": _call_anthropic, "claude-cli": _call_claude_cli}`。
- 新增缓存目录覆盖：`CACHE_DIR = Path(os.environ.get("GATEKEEPER_CACHE_DIR", ROOT/"data"/"cache"))`。用途：在新通道上跑 `--no-cache` 评测时写到 `data/cache_openai/`，**不覆盖随仓库分发的示例缓存**。
- `call_model` 返回的 `source` 值加 `openai`；前端 `SRC` 映射加 `openai:'OpenAI 兼容 API'`。

**server.py**
- `/api/meta` 返回 `{"provider", "model"}` 时，`model` 按实际通道给：openai → `OPENAI_MODEL`，其他不变。

**文件**
- `.env.example`：四个变量占位（`GATEKEEPER_PROVIDER=openai`、`GATEKEEPER_OPENAI_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1`、`GATEKEEPER_OPENAI_API_KEY=替换为你的密钥`、`GATEKEEPER_OPENAI_MODEL=mimo-v2.5-pro`）。`.gitignore` 加 `.env`、`data/cache_*/`。
- `requirements.txt` 加 `openai`。README 环境变量表加四行。

**验收**
- `GATEKEEPER_PROVIDER=openai GATEKEEPER_CACHE_DIR=data/cache_openai python eval.py --no-cache` 跑完，记录每章耗时、召回、误报（写入 T7）。若某章 `parse_json` 失败，打印原始输出前 300 字并重试一次（在 `extract_chapter/check_chapter` 外层，`call_model` 里加 `retries=1` 参数，只对 openai 通道）。
- `python eval.py`（不带参数）仍然全 `[cache]`、9/10。
- 提交：`feat: openai-compatible provider, cache dir override`。

### T2 · 长任务进度、计时、可中断（20 分钟，纯前端）

- 「建立设定库」循环（`#btnExtractAll` onclick）改为：显示 `第 i/N 章 · 已用 Xs`（`setInterval` 每秒刷新到 `#midStatus`），按钮文字变「停止」，点击置 `state.stop=true`，循环在下一章开始前检查并退出，状态栏写「已停止，已抽取 i 章，可再点继续」。已抽取的章节 `extracted=true` 已由服务端持久化，续跑天然从断点开始。
- 每章抽取完在章节列表的 `.chap .n` 后追加 `· 42s · 缓存/openai`（把 `extractOne` 返回的耗时与 `r.source` 存到 `state.timing[i]`，`renderProject()` 读）。
- 「检查矛盾」同样显示秒表；失败时状态栏给「重试」按钮（重新触发同一请求）。
- 验收：手动在 `GATEKEEPER_PROVIDER=openai` 下点建立设定库，中途点停止，再点继续，最终 15 实体 60 事实（缓存命中时数字相同）。
- 提交：`feat: progress timer and stop/resume for extraction`。

### T3 · 结构化「有意为之」规则与程序级过滤（45 分钟）

现状：`resolve` 路由把 `"{entry}：{fact} ⇄ {conflict_quote}"` 文本存进 `project["intentional"]`，随 prompt 传给模型，靠模型自觉不报。改为**程序过滤为主，prompt 提示为辅**。

**数据结构**（`project["rules"]`，替代 `project["intentional"]`；`load()` 时若旧项目无 `rules` 则从 `intentional` 迁移）
```json
{"id":"8位hex","kind":"intentional","entry":"林砚","fact":"第1章时十七岁…","bible_quote":"…","note":"作者备注，可空","from_finding":"fid","created":"ISO时间"}
```

**server.py**
- `resolve` 路由：`status=="intentional"` 时创建规则（同 entry+fact 已存在则复用）；请求体 `ResolveReq` 加可选 `note: str = ""`。`status` 改回 `open` 时删除该 finding 创建的规则。
- 新增 `GET /api/projects/{pid}/rules`、`DELETE /api/projects/{pid}/rules/{rid}`。
- `check` 路由：调用 `engine.check_chapter` 后，对每条 finding 执行 `engine.match_rule(finding, rules)`；命中则 `status="intentional"`、`rule_id=rid`，**仍返回**但前端折叠显示。传给 prompt 的 `intentional` 列表仍用规则生成同样的 `"{entry}：{fact} ⇄ …"` 文本（格式不变，prompt 不变）。

**engine.py**
- `match_rule(finding, rules) -> str | None`：同 `bible_entry`，且（`bible_fact` 相等 或 `bible_quote` 互为子串 或 两者去空白后最长公共子串 ≥ 12 字）。用 `difflib.SequenceMatcher(None, a, b).find_longest_match()` 即可，不引依赖。
- 单元断言写进 `tests/smoke_api.py`：标记一条为 intentional 后，再次 `POST /check`（缓存命中，同一批 findings）→ 该条 `status=="intentional"` 且带 `rule_id`，其他 8 条 `open`。

**index.html**
- 「有意为之」按钮点击后弹 `prompt()` 让作者填一句备注（可空）。
- 设定库页顶部新增折叠块「规则 N 条」：列出 entry / fact / note / 删除。
- 矛盾清单：`status=="intentional"` 且有 `rule_id` 的条目收进底部「已按规则跳过 N 条（展开）」。
- 提交：`feat: structured intentional rules with server-side matching`。

### T4 · 状态变更标记与时间线视图（40 分钟，不改 prompt）

- `engine.merge_entries()`：追加事实时用正则 `re.search(r"由(.{1,30}?)变为(.{1,30})", fact)` 识别变更，命中则 `f["change"] = {"from": g1, "to": g2}`；另外若 fact 以「第N章」开头（抽取 prompt 已要求写「第3章…」这类前缀）解析 `f["chapter_no"]`，否则用当前章序号。**不改 prompt，只解析已有输出**。示例缓存回放后应识别出至少 2 条变更（林砚「由右手使刀变为左手使刀」等）。
- 中栏新增 tab「时间线」（`.tabs` 加按钮，`#tab-timeline`）：
  - 上半部：按章序列出 `type=="timeline"` 实体的 facts（即「第N章」实体），每章一组。
  - 下半部「状态变更链」：按实体分组，`change` 事实按 `chapter_no` 排序渲染为 `第1章 右手使刀 → 第3章 左手使刀`，点击可 `showQuote()` 定位原文。
- 设定库页里 `change` 事实前加 pill「变更」。
- 验收：载入示例 → 建立设定库（缓存）→ 时间线 tab 有 5 组事件、变更链至少包含林砚与青玉佩两条。`smoke_api.py` 断言 `sum(1 for e in bible for f in e["facts"] if f.get("change")) >= 2`。
- 提交：`feat: timeline tab and state-change chains`。

### T5 · 切章容错 + 设定库手工增改（30 分钟）

**切章**（`engine.split_chapters`）
- `CHAPTER_RE` 扩展：`第N章/回/节`、`Chapter N`、`N.`/`N、` 行首且整行 ≤ 30 字、`卷N`；另支持手动分隔行 `^===+$`。保持 `split_chapters(sample.manuscript)` 仍是 5 章、标题不变（否则缓存失效，用 `eval.py` 守住）。
- 单章正文 > 6000 字时，`create_project` 返回 `warnings: ["第3章 7200 字，超出建议长度，抽取可能变慢或截断"]`，前端状态栏显示。

**设定库手工增改**
- `POST /api/projects/{pid}/bible/facts` 体 `{type, name, fact, chapter, quote?}` → 复用 `merge_entries()`（把它包成单条 entries 调用），`quote_verified` 按所在章正文核验。
- `PATCH /api/projects/{pid}/bible/{eid}/facts/{fid}` 体 `{fact?, quote?}` 修改文本，重新核验 quote。
- 前端：设定库页「+ 手工添加事实」小表单（类型下拉、实体名、事实、来源章下拉）；每条事实双击进入行内编辑，回车保存。
- 提交：`feat: heading variants, manual bible edits`。

### T6 · 导出核对报告（25 分钟）

- `GET /api/projects/{pid}/export.md`：Markdown，含作品名、章节列表、设定库（按类型分组，每条附来源章与引用、变更用 →）、规则列表、最近一次检查的矛盾清单（按严重度，含状态、设定引用、新章引用、建议）。`Response(media_type="text/markdown; charset=utf-8")`，`Content-Disposition: attachment; filename*=UTF-8''...`。
- 前端左栏标题行加「导出 .md」按钮，`window.open` 该 URL。
- `smoke_api.py` 断言导出文本包含「林砚」与「第六章 云州夜雨」。
- 提交：`feat: markdown export`。

### T7 · 真实评测、README、截图、提交（35 分钟）

1. `GATEKEEPER_PROVIDER=openai GATEKEEPER_CACHE_DIR=data/cache_openai python eval.py --no-cache > data/eval_openai.log`，把结果表填进 README「当前状态」新增一行（模型 `mimo-v2.5-pro`，召回/误报/陷阱/引用命中/耗时）。若召回 < 7，如实写，并在「下一步」加一条「换模型后 prompt 需针对性调整」。**不要**用它覆盖 `data/cache/`。
2. README 更新：环境变量表（T1）、用户路径（加规则、时间线、手工编辑、导出）、「真实/缓存/没做」三段、目录树；「没做」里明确：规则匹配是字符串启发式不是语义匹配；时间线依赖模型输出的「第N章」前缀；导出不含历史检查。
3. 截图：`docs/shot6_timeline.png`、`shot7_rules.png`（浏览器手动截图即可）。
4. `python eval.py` + `python tests/smoke_api.py` 全绿后最终提交：`docs: real eval on openai provider, README update`。

## 验证方式（总）

- 离线回归：`python eval.py` → 召回 9/10、误报 1、陷阱 0、引用 9/9，所有来源 `[cache]`（证明 prompt 与切章未被改坏）。
- 全路径冒烟：`python tests/smoke_api.py` 退出码 0（覆盖 T0/T3/T4/T6 断言）。
- 真实调用：`GATEKEEPER_PROVIDER=openai GATEKEEPER_CACHE_DIR=data/cache_openai python eval.py --no-cache` 跑完并记录。
- 手动 UI：`python -m uvicorn app.server:app --port 8765`，走一遍：载入示例 → 切分 → 建立设定库（中途停止/继续）→ 时间线 tab → 新章节检查 → 标记一条有意为之（填备注）→ 再检查一次，该条被折叠为「已按规则跳过」→ 手工加一条事实 → 导出 .md。
- 每个任务一次 commit，最终 `git log` 应有 7–8 条。

## 交付清单

- 代码与提交（上述 7 个任务）
- `tests/smoke_api.py`、`.env.example`
- README「当前状态」含两行真实评测（claude -p 与 openai 通道）
- `docs/shot6_timeline.png`、`docs/shot7_rules.png`
