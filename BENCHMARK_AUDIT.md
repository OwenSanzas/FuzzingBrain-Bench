# FuzzingBrain-Bench — 代码审计

对公开 benchmark 包(`fbbench/` 约 5.8k 行、34 个文件,外加 `tools/`)做的完整通读,由六个并行审查器分别覆盖:episode/MCP 核心、provider 后端 + 成本、sweep 编排、prompts + 评分、CLI/grading/配置,以及整包死代码扫描。

每条发现的格式为 `位置 → 问题 → 后果`。严重度取审查器判断,略作统一。本报告未改动任何代码,仅为发现清单。

> HIGH 集中有两条贯穿主线:
> 1. **"负责盲评的代码"并没有真正保住盲评。** "封 verdict"的保证完全落在 runner 侧的过滤上;grading 层本身原样返回完整 verdict,而且已经存在至少两条侧信道(一个 nudge、一条出错路径),让模型能反推出被封的结果。
> 2. **失败被静默变成 0 分,然后被冻结。** oracle/网络一抖动就被 `except` 吞掉、写成一个"合法"的 `solved=False, tier=0`,而 resume 逻辑永不重试——于是一次故障就永久污染榜单。

| 严重度 | 数量 |
|---|---|
| HIGH | 9 |
| MEDIUM | 15 |
| LOW | 12 |
| 死代码(多为 low,少数为潜在陷阱) | 16 |

---

## 交叉核验结果(对抗式复核)

对每条发现回到真实代码做了"设法证伪"的独立复核。结论:**52 条中 2 条为 FALSE POSITIVE,2 条降级为 PARTIAL,其余 CONFIRMED**;另有 4 条虽成立但需修正定性/范围。下方每条已就地标注 `[FP]` / `[PARTIAL]` / `[已核验·修正]`。

| 判定 | 条目 |
|---|---|
| **FALSE POSITIVE(定性错,应剔除或大幅降级)** | **M2**(sanitizer 是 runner 故意 backfill 的公开信息,非泄题)、**M6**(`Completion.text` 恒为 `str`,不会 None,`.upper()` 不崩) |
| **PARTIAL(机制成立,但危害被高估/范围更窄)** | **M1**(出错转发机制真,但"error.data 含 verdict"无法证实、罕见)、**H3**(`grade_blob` 仅人工 `grade` CLI 调用,模型看不到;属潜在脆弱非活跃泄漏) |
| **CONFIRMED 但需修正** | **M13**(更严重:所有 bench.yaml 都没有 `capability_set` 键,当前每个 bug 已在按完整 5 档回退——是活行为)、**H2**(可由 `BENCH_GRADE_URL` 环境变量覆盖,但无变量时该脆弱默认仍生效)、**H9**(静默路径特指 `resp.candidates` 整体为空的 prompt 级拦截)、**M5**(代码成立,但当前 episode 循环下空 assistant 回合会先终止,基本触达不到) |
| **其余全部 CONFIRMED** | H1,H4,H5,H6,H7,H8,M3,M4,M7,M8,M9,M10,M11,M12,M14,M15,L1–L12,D1–D16 |

> 最需要注意的翻转:原报告里两条 MEDIUM 安全/健壮性发现(M2 泄题、M6 崩溃)是**误报**;而 M13 实际比写的更糟(不是"潜在",是所有 bug 现在就在错误地按满档评分)。

---

## HIGH

### H1 — oracle 评分失败被吞掉、写成真实 0 分,再被 resume 永久跳过
- **`fbbench/sweep/codex.py:398`**(`_best_caps`,codex 与 claudecode 两个 arm 共用)
- `_remote_grade` 失败(oracle 挂、5xx、网络抖动、超时)落入 `except Exception: continue`。若所有 blob 都失败,该 cell 记为全 `not_fired`、`tier_score=0`、`solved=False`——与真正没解出来完全无法区分。`run_cell` 照样写 `score.json`,而 `run_matrix` 的 resume(`orchestrator.py:189,237`)会跳过任何已有 `score.json` 的 cell。
- **后果:** 一次瞬时 oracle 故障把已解出/高档位的尝试永久变成 0,且永不重试——静默污染整轮 sweep 的榜单。

我：

如果说n次调用全部失败，且是因为后端出问题，则标记为network_error，但还是要保留not_fire

哪怕有一次成功，都不算network_error



### H2 — grade oracle 是硬编码的临时免费 ngrok URL,且无任何备用 · `[已核验·修正]`
> 核验:可由 `BENCH_GRADE_URL` 环境变量覆盖(grader.py:34);但不设该变量时,这个免费档默认值就是唯一真源,脆弱性成立。
- **`fbbench/grading/grader.py:21`**
- 唯一的默认评分端点是 `https://nonretinal-arletha-arduous.ngrok-free.dev`(免费档隧道:重启即变、有速率限制)。它是唯一真源,所有 `grade`/`run`/sweep 都走它。
- **后果:** 隧道一重启或撞上免费档上限,全线评分中断。对一个把该 URL 写进代码的**公开**库来说,这是必然会发生的未来故障(且见 M12:无鉴权)。

### H3 — `grade_blob` 原样返回完整 verdict;封 verdict 完全交给 runner 代码 · `[PARTIAL — 已核验]`
> 核验:数据流属实,但 `grade_blob` 唯一调用方是人工 `grade` CLI(结果打到操作者终端),runner/sweep 均不 import 它,模型看不到。属潜在脆弱(若将来有人转发就漏),非活跃泄漏 → 建议降为 MEDIUM。
- **`fbbench/grading/grader.py:24-50`**
- 直接返回 oracle 原始 JSON(`capabilities`、`capabilities_bestof`、`target_bug_found`、`agreed`、`evidence`、`harness_output`),没有任何 agent-safe 变体。agent 自己的 `run_poc_on_harness` 工具打的是同一个 `/run_poc_on_harness` 端点(`runner/mcp_client.py`、`sweep/codex.py:73`)。`grading/` 里没有任何剥离。
- **后果:** 盲评的封印 100% 依赖 grading 层之外的过滤。任何 runner 侧改动(或任何新调用方转发响应)都会把 `target_bug_found`/`agreed`/`capabilities` 直接漏给模型。见 H4/M1——侧信道已经存在。

### H4 — `--no-stop-on-solve` 下,`KEEP_HUNTING_NUDGE` 的有无泄露 `target_bug_found`
- **`fbbench/runner/episode.py:332-357`**
- 仅当"崩溃触发了但不是 target"时才追加 nudge(`crashed_hit` 是 `target_found` 的 `elif`)。`--no-stop-on-solve` 下命中后 episode 继续,模型在自己的 `harness_output` 里看到崩溃却**没收到** nudge——"nudge 缺席"这一点就区分了"崩溃但非 target"与"崩溃且就是 target"。
- **后果:** 模型仅凭是否被 nudge 就能推断被封的 verdict,击穿盲评。(默认 `stop_on_solve=True` 会在下一次模型调用前结束 episode,从而掩盖此问题。)

### H5 — MCP 客户端 `readline()` 无超时,且 episode 没有 wall-clock 停止
- **`fbbench/runner/mcp_client.py:142`**
- `_call` 写完请求后阻塞在 `stdout.readline()`,无超时/无 deadline。`EXEC_TIMEOUT_CAP_S` 只改写发给服务端的**参数**。`run_episode` 只数 `max_turns`——没有 wall-clock 上限,尽管 docstring 声称按 wall-clock "solve 或 budget 即停"。
- **后果:** 只要服务端卡住、远端 oracle POST 阻塞、或容器卡死,客户端就**永久**阻塞并占死一个 worker;turn budget、wall clock、exec cap 都救不出来。

### H6 — dashboard 路径从不排空 runner 的 stderr 管道 → 死锁、烧满 timeout
- **`fbbench/sweep/dashboard.py:412`**(`run_cell_tailing`,默认的单跑 `--arm api` 路径)
- runner 以 `stderr=subprocess.PIPE` 启动;轮询循环只读 `episode.jsonl`,从不读 `proc.stderr`,`proc.wait()` 也不排空它。子进程往 stderr 写满约 64KB 后,OS 管道缓冲填满,runner 阻塞在 `write()`。
- **后果:** 子进程卡死,dashboard 显示该 cell "running" 但毫无进展,只有 wall-clock timeout 把它杀掉才回收——每个长/啰嗦的 episode 都静默烧满整个 timeout,记为 `{"error":"timeout"}`。

### H7 — 超时/kill 时泄漏 docker 容器及 runner 的孙进程(所有 arm)
- **`fbbench/sweep/orchestrator.py:104`**(以及 `dashboard.py:441`、`codex.py:182`、`claudecode.py:183`)
- API arm 用 `subprocess.run(..., timeout=...)` 且没有 `start_new_session`;`TimeoutExpired` 时 Python 只 SIGKILL 直接子进程(`python -m fbbench.runner`),把它启动的 `docker run` 容器孤儿化。CLI arm 会 `_kill_pg` docker 客户端,但杀掉一个 attach 的 `docker run -i --rm` 并不能可靠停止/`--rm` 清理由 dockerd 持有的容器。
- **后果:** 大规模并行 sweep 下,超时/被杀的 cell 不断堆积孤儿容器,逐步耗尽(本就很小的)主机 RAM/CPU,并卡住后续 cell。

### H8 — 单个 cell 抛异常拖垮整个 matrix;codex arm 还泄漏临时目录
- **`fbbench/sweep/orchestrator.py:254`** 和 **`fbbench/sweep/codex.py:615`**
- 无论并行 `list(ex.map(_run_one, todo))` 还是串行分发,都没有把 `_cell` 包在 try/except 里。任何未捕获的 arm 异常(docker 缺失、未捕获的 oracle 错误、`orchestrator.py:109` 里 corrupt 的 `score.json`)都会冒泡并在 `aggregate`/`write_summary` 之前杀掉 `run_matrix`。codex 的 `run_cell` 没有 try/finally——`shutil.rmtree` 在最末尾,中途异常就泄漏临时目录(含 symlink 的 `auth.json` + workspace)。`claudecode.py:495-503` 用了正确的 try/finally;codex 没有。
- **后果:** 多小时 sweep 快结束时一个坏 cell 会丢掉**所有** cell 的汇总表 + summary;反复的 codex 失败会堆积临时目录直到磁盘写满。

### H9 — Gemini 被拦截/空响应被当作合法的空回合返回,且无 error · `[已核验·修正]`
> 核验:静默路径特指 `resp.candidates` **整体为空**(prompt 级拦截);若候选存在但 `finish_reason` 为 SAFETY/RECITATION/MAX_TOKENS,`stop_reason` 会被设上、循环可检测。核心问题(prompt 级空返回无信号)成立。
- **`fbbench/runner/backends/gemini_backend.py:115-131`**
- 当 `resp.candidates` 为空(safety/`RECITATION`/`MAX_TOKENS` 拦截,或候选无 parts),返回 `Completion(text="", tool_calls=[], stop_reason="")` 且**无 error**。什么都不抛。
- **后果:** episode 循环无法区分"被过滤/失败的生成"与"模型合法地不调用工具就结束",于是静默终止或误评该 cell——结果被污染却无任何日志信号、无重试。

---

## MEDIUM

### M1 — grade 工具的**出错**路径绕过 `harness_output`-only 过滤 · `[PARTIAL — 已核验]`
> 核验:转发未剥离的 `{error,data}` 机制属实;但"error.data 是否真含 verdict 素材"无法从本库证实(正常评分走 `structuredContent` 而非 RPC error),实际泄漏较投机。代码脆弱成立,危害存疑。
- **`fbbench/runner/episode.py:272-276,338`** — 剥离守卫的条件带了 `not is_error`;一个抛出 `MCPToolError` 的 `run_poc_on_harness` 会落到 `else`,把整个 `{"error":…, "data": e.data}` 原样转发。
- **后果:** `BENCH_GRADE_REVEAL=1` 下服务端塞进 grade 出错 `data` 里的任何 verdict/诊断信息都会未剥离地进入模型上下文——出错路径上的泄题,违背"只有 harness_output"的契约。

### M2 — sanitizer 逃过客户端"安全"redactor 且被输出两次 · `[FALSE POSITIVE — 已核验]`
> 核验:机制(白名单留 sanitizer、输出两次)属实,但**定性错误**:runner 的 `backfill_sanitizer`(episode.py:101-112)**故意**把 sanitizer 补给模型,其 docstring 明说这是"模型必须拥有的公开 setup 信息";且 sanitizer≠class 档答案(一个 ASan 映射多种 class,从不指明哪个触发)。是冗余,不是泄题。**应剔除。**
- **`fbbench/prompts.py:295`**(以及 267-268) — `_FULLSCAN_SETUP_KEYS` 保留了 `"sanitizer"`,`build_env_block()` 也会打印它,于是只要 `setup_resp` 带了它,模型就被告知 sanitizer 两次;那个名义上负责脱敏的守卫函数本身从不剥离它。
- **后果:** 点名 sanitizer 会大幅收窄(对 UBSan/LSan/Jazzer 几乎等于直接揭示)agent 本应自己发现的 `class` 档位。盲评保证完全寄托于 Go 服务端不发这个字段;任何 misconfig/未来改动都会静默转发。

### M3 — 回退版"solved"在高档位只是缺席时把部分梯子判成 solved
- **`fbbench/report/summary.py:53`** 和 **`fbbench/runner/report.py:344`** — 旧回退逻辑:`bool(applicable) and all(v=="fired")`,其中 `applicable` = caps 去掉 `"n/a"`。如果未触达的档位是被**省略**而非记 `"not_fired"`,一个只 reach 的 cell 就被判 SOLVED。
- **后果:** 任何缺少新版显式 `solved` 字段的运行都会评分错误——只 reach/只 crash 的 episode 被算作完整 solve,虚高 Score 与解题矩阵。

### M4 — Gemini 丢弃 cached-content token → 每次命中缓存都超额计费
- **`fbbench/runner/backends/gemini_backend.py:132-136`** — 设了 `input_tokens = prompt_token_count` 却从不读 `cached_content_token_count`,所以 `cache_read_tokens` 恒为 0。Gemini 的 `prompt_token_count` **包含**缓存前缀,于是缓存部分按全价 1x 而非 0.25x 读价计。
- **后果:** 每个命中缓存的 Gemini 回合成本都被高估;`pricing.py` 里那个 0.25 的 Gemini 缓存倍率实际成了死代码。

### M5 — OpenAI 空 assistant 回合生成一条 API 非法的消息 · `[已核验·修正]`
> 核验:代码差异属实(OpenAI 不补 `"(no output)"` 占位,另两家补);但当前 episode 循环下空 assistant 回合会先触发终止,该非法消息基本不会被再次发出——属潜在 bug。
- **`fbbench/runner/backends/openai_backend.py:61-68`** — 既无 text 又无 tool call 的 assistant 历史条目产出 `{"role":"assistant","content":null}` 且无 `tool_calls`,Chat Completions API 会拒绝。Anthropic 和 Gemini 后端会插入 `"(no output)"` 占位;OpenAI 不会。
- **后果:** 任何一旦记录过空 assistant 回合的 episode 会在下一次 OpenAI 调用崩溃,而同样的历史在另外两家能正常跑——中途 abort 一个 sweep cell。

### M6 — `text` 为 None 时 `comp.text.upper()` 崩溃 · `[FALSE POSITIVE — 已核验]`
> 核验:`Completion.text` 默认 `""`(base.py:38),三家后端都只做字符串拼接或 `or ""`,**没有任何路径能返回 `text=None`**,`.upper()` 不会抛 `AttributeError`。缺 None 守卫只是表面性,非缺陷。**应剔除。**
- **`fbbench/runner/episode.py:258`** — 主动停止检查无条件调 `comp.text.upper()`;后端返回 `text=None` 且无 tool call 时抛 `AttributeError`(对比 `_is_refusal`/`_is_truncated` 正确地用 `comp.stop_reason or ""` 兜底)。
- **后果:** 一个良性的"不调工具"回合被外层 `except` 捕获、误标为 `terminated_reason="error"`,污染停止原因统计。

### M7 — JSON-RPC 响应不做 id 匹配;一行杂散 stdout 就破坏调用
- **`fbbench/runner/mcp_client.py:142-149`** — `_call` 假定下一行 stdout 就是匹配的响应,从不检查 `resp["id"]`,并直接取 `resp["result"]`。任何 notification/进度/日志行进到 stdout → `KeyError` 或静默的错配响应。
- **后果:** 一行意外的 stdout 就让请求/响应流失步——要么把 episode 杀成 `error`,更糟的是把某个响应配给错误的请求。

### M8 — 强杀关闭路径上泄漏容器/进程
- **`fbbench/runner/mcp_client.py:158-161`** — `close()` 中若 `terminate()`+`wait(5)` 失败,调了 `kill()` 却从不 `wait()`;SIGKILL 掉 `docker run --rm` 客户端会孤儿化容器并留下僵尸。
- **后果:** 大规模 sweep 下,卡死的 episode 累积孤儿容器 + 僵尸 docker 客户端。

### M9 — `aggregate`/resume 计数解析 `score.json` 无错误处理
- **`fbbench/sweep/orchestrator.py:134`**(及 189,237,259) — `json.loads(sj.read_text())` 无保护;`score.json` 非原子写入,中途被杀或截断即产生非法 JSON。只有收尾的 `spent` 统计包了 try。
- **后果:** 一个 corrupt/部分写入的 `score.json` 就让最终 `aggregate` 抛异常,丢掉一整轮本已跑完 sweep 的榜单 + 报告。

### M10 — 反作弊守卫只覆盖 claudecode、手动、且会误报 FAIL
- **`fbbench/sweep/test_no_cheat.py:73`** — 只测 claudecode arm;codex 那套不同的 hardening(`config.toml`)和 API arm 都没有对应证明。它是个烧 token 的手动脚本(非 pytest/CI),且 PASS 条件要求对照模型**真的泄露**(`ctl_leaked and not hard_leaked`),所以对照模型一旦拒答/失败,即便 hardening 正确也报 FAIL。
- **后果:** codex arm 的 hardening 回退(比如 `config.toml` 某个键改名悄悄重新启用了 shell 工具)会无人察觉;唯一的守卫窄、易跳过、还不稳定。

### M11 — 容器内 `exec` 读取 oracle 答案是一条未被测试的残余泄题面
- **`fbbench/sweep/claudecode.py:30`**(两个 CLI arm 都承认) — 没有任何东西验证答案/oracle 材料不在 `fbbench-challenge-<alias>` 镜像里、或不能被 `mcp__bench__exec` 读到;`cheated_web`(codex.py:571)只盯 web 字符串,不管读答案文件。
- **后果:** 只要有哪个挑战镜像在磁盘上带了答案数据,agent 就能用受祝福的 `exec` 读到,不靠真实能力就"解出"——没有守卫能抓到。

### M12 — grade POST 无鉴权
- **`fbbench/grading/grader.py:36-40`** — 请求只带 `Content-Type` + `ngrok-skip-browser-warning`;URL 又写在公开库里。
- **后果:** 任何人都能无限 POST blob(DoS/成本/滥用);无法归因或限速。与 H2 叠加。

### M13 — `read_bench` 静默丢弃块状/嵌套 YAML → 回退到完整 5 档梯子 · `[已核验·比原述更严重]`
> 核验:不仅是潜在——检查全部 69 个 `bugs/*/bench.yaml`,**没有一个含 `capability_set` 键**,所以 `capability_set()` 现在对**每个 bug**都返回完整 `DEFAULT_KB`(活行为,非隐患)。块状 YAML 触发条件另属潜在。**建议升级关注。**
- **`fbbench/grading/bench_yaml.py:42-61`** — 只捕获顶层标量和单行 `[a,b]` 列表(跳过以空格/tab/`-` 开头的行)。块状列表写法的 `capability_set`(或拼错/缺失的键)会被静默忽略,`capability_set()` 随即返回完整 `DEFAULT_KB`。
- **后果:** 真实 K_b 只是子集的 bug 被按全部五档评分/展示 → 错误的 PASS/FAIL 与错误榜单,且无报错。

### M14 — `cmd_grade` 在畸形 200 响应上崩溃
- **`fbbench/cli/commands.py:89`** — `caps = r["capabilities"]` 是裸取,位于 try/except **之后**;一个缺 `capabilities` 的 200 响应抛未捕获的 `KeyError`。
- **后果:** 用户拿到原始 traceback + 通用崩溃退出码,而非本应的干净 `grade failed: …` / 评分 0/1。

### M15 — `resolve_output`:与路径段同名的裸名字导致令人困惑的嵌套
- **`fbbench/paths.py:48-51`** — 不含 `/` 的值被当作 campaign 名放到 `output/` 下,于是 `--output output` → `output/output`;用户本想当 cwd 相对目录的裸名字会落到 `REPO/output/`。裸名字也无任何清洗。
- **后果:** 结果静默落到意外目录;用户拿 `--output output` 重跑会写到 `output/output`,于是 resume 看似失效、旧结果看似丢失。

---

## LOW

### L1 — 大 `write_file` 时 stdin/stdout 管道死锁
- **`fbbench/runner/mcp_client.py:139-142`** — 请求整体写完+flush 后才读 stdout;`write_file` 载荷超过管道缓冲时,若服务端在 stdin 排空前就开始响应可死锁(stdout 无线程排空;stderr 有)。典型 PoC 尺寸不太会遇到。

### L2 — 整个容器生命周期内 stderr 缓冲无上限
- **`fbbench/runner/mcp_client.py:81-87`** — `_drain_stderr` 全程逐行追加、无上限;实际只用到最后 20 行。

### L3 — Gemini 重试分类过宽,会重试可能已成功的调用
- **`fbbench/runner/backends/gemini_backend.py:23,27-41`** — `_TRANSIENT` 在小写文本里任意位置匹配裸子串(`"500"` 命中任何含这几位数字的文本;`"rate"` 命中无关词),并在 `timeout`/`deadline` 上重试,可能重发一次已在服务端跑过的生成。有上限(约 6 次)。

### L4 — 对无法路由的 id,`cost_usd` 会抛异常而非报告"未定价"
- **`fbbench/models/pricing.py:90,104`** — 在 `pricing_known=False` 回退之前先调 `provider_for(model)`(前缀无法路由时抛 `ValueError`);一个通过显式后端跑出、但前缀不识别的合法运行会让成本报告崩溃,而非优雅降级为 `total_usd=None`。

### L5 — OpenAI 因长度截断的回合与"不调工具"无法区分
- **`fbbench/runner/backends/openai_backend.py:101-109`** — `finish_reason=="length"` 返回 `text="", tool_calls=[]`;`stop_reason` 带了 `"length"` 因此可检测,但此处不标记空结果。仅当循环纯以"无 tool call"为判据时才有风险。

### L6 — `cheated_web` 记录了却从不使结果作废;claudecode 根本不记
- **`fbbench/sweep/codex.py:598`** — codex 把 `cheated_web` 写进 `score.json`,但 `aggregate`/评分从不参考;claudecode 连这个标记都不算。
- **后果:** 即便检测到作弊面,被污染的结果仍算作有效;下游无任何强制/过滤。

### L7 — 中途 nudge 让模型调 `grade()`,但工具叫 `run_poc_on_harness`
- **`fbbench/sweep/codex.py:206`**(及 `claudecode.py:87`) — `_codex_nudge`/`_budget_text` 反复说 "call grade()";根本没有这个工具(提交工具是 `run_poc_on_harness`)。只有 codex 初始 budget 用了正确名字。
- **后果:** nudge 指向不存在的工具,可能压低 grade 调用频率(进而压低计分能力),在 resume 多的 episode 上尤甚。

### L8 — dashboard 路径打印的"total"成本不含被 resume 的 cell
- **`fbbench/sweep/orchestrator.py:282`** — 串行 dashboard 路径用 `spent = STATUS.total_cost`(只累计本次跑完的 cell),而文案却说 "all cells on disk"。并行/非 dashboard 路径从磁盘重算是对的。

### L9 — `find_bug`/`bug_id` 未清洗:路径穿越 + URL query 未转义
- **`fbbench/grading/bench_yaml.py:64-73`、`fbbench/grading/grader.py:33-37`** — `proj / bug_id` 无校验(含 `../`/绝对路径的 id 可逃出 `bugs/`);同一 `bug_id` 未 percent-encode 就插进 `?bug={bug_id}`。因 `bug_id` 是人工 CLI 参数,列为 low。

### L10 — 即使适用档位不足 5,tier 也渲染成 `X/5`;还可能出现 `None/5`
- **`fbbench/runner/report.py:502,386`** — 恒除以 5;一个只有 3 个适用档、已全解的 bug 显示 "3/5"(读起来像没做完),且 `tier_bestof` 可能为 `None` → "None/5"。

### L11 — `build_env_block` 对所有非 JVM 语言硬编码 clang/clang++
- **`fbbench/prompts.py:263,259`** — 仅按 C-vs-C++ 选 `clang`/`clang++`,于是带 sanitizer 标记的 Rust/Go/Python 目标会被告知用 `clang -fsanitize=…` 构建。误导性(非泄题)。

### L12 — 即便是子集 sweep,`max_score` 也是全语料总分
- **`fbbench/report/summary.py:33`**(在 85/140 使用) — `_load_difficulty()` 对全部 68 个 bug 求和后原样传出,不管本次 sweep 实际含哪些 bug,于是部分 sweep 的 Score/max_score 分母偏大。可能是有意(绝对分 vs 全语料)。

---

## 死代码

**未发现 should-be-wired(本该接线却成死代码)的 bug**——所有安全/停止守卫(`_clamp_exec_timeout`/`EXEC_TIMEOUT_CAP_S`、cheat-hardening 标志、`stop_on_solve`、`solved_hit` 跳出)都确实被调用。下列都是遗留代码;其中两个是潜在陷阱。

**潜在陷阱(medium):**
- **D1 `grade_blob(rounds=…)` 收下却从不使用** — `grading/grader.py:24`。调用方传 `rounds=5` 会静默拿到服务端默认(1),无报错。应删掉该参数或接线进请求。
- **D2 截断重试分支不可达;`TRUNCATION_NUDGE` prompt 现已死** — `runner/episode.py:250` + `prompts.py:345`。`if consecutive_trunc >= 1:`(247 行)恒为真(计数器每个工具回合归零、且紧接检查前才自增),所以 250-255 行(追加 nudge、`continue`)永不执行。从 `>= 5` 改成 `>= 1` 是有意的,但把该分支和已注册的 prompt 变成孤儿——而它**仍出现在生成的 `docs/PROMPTS.md` 里像是活的**。效果:截断重试实际被禁用。

**死函数 / 渲染器(medium):**
- **D3 `_grade_calls()`** — `sweep/codex.py:418`,零调用(真实值来自 `_rollout_stats`)。
- **D4 `render_text()`** — `runner/traj.py:135`,零调用(只用 `render_md`)。

**死字段 / 死参数 / 死常量(low):**
- **D5 `EpisodeResult.last_grade`** — `runner/episode.py:82`,每次 grade 在 :281 赋值,从不读取。
- **D6 `LADDER`** — `report/summary.py:20`,定义后从不使用(该模块的列由 caps 推导)。
- **D7 `_ladder_html(kb=…)`** — `runner/report.py:55`,参数从不读取(注释说明是有意的)。
- **D8 `stage_claude_env(model=…)`** — `sweep/claudecode.py:111`,参数未用。
- **D9 `stage_codex_env(bug=…)`** — `sweep/codex.py:113`,函数体内未用该参数。
- **D10 `list_bugs(include_inactive=…)`** — `grading/bench_yaml.py:90`,从未被传 `True`。
- **D11 grading 导出的 `FLAGS`** — `grading/grader.py:17`(+ `__init__` 再导出),无 importer 使用;是梯子列表的第三份拷贝。
- **D12 `build_diffscan_message()` 桩 + `--mode diffscan`** — `prompts.py:540`;桩 `raise NotImplementedError`,且 CLI 的 `diffscan` 选项只会产出一个 error 运行(`episode.py:149`)。是有文档的扩展点;在接线前建议隐藏该选项。

**死导入 / 表面性(low):**
- **D13 `yellow`** — 死 helper `cli/console.py:26` + 未用导入 `cli/commands.py:10`。
- **D14 `import subprocess`** — `cli/commands.py:5`,未使用。
- **D15 `import yaml`** — `tools/sealed/verify_sealed.py:16`,未使用(让 PyYAML 成了隐形幽灵依赖)。
- **D16 `raise last  # unreachable`** — `runner/backends/gemini_backend.py:41`,永不执行(循环总是先 return/re-raise)。

---

## 干净(未发现真实问题)

`runner/traj.py`(只汇总已剥离的 transcript)、`runner/backends/base.py`、`runner/backends/anthropic_backend.py`(usage 分桶正确、max_tokens 学习重试有守卫、空内容有占位、缓存断点 ≤4)、`runner/backends/__init__.py`、`models/catalog.py` + `models/__init__.py`(路由与 per-1M 单位正确、local→$0、未定价→`total_usd=None`)、`cli/main.py`(子命令接线正确、互斥 dashboard 组 + 子解析器 `required=True`)、`cli/console.py`、`cli/__init__.py`、`env.py`(只打印变量**名**、`os.environ` 优先于 `.env`)、`grading/__init__.py`、`__init__.py`、`__main__.py`、`report/__init__.py`、`pyproject.toml`(entry point 匹配)、`fb-bench` 启动脚本。
