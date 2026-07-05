# 翻译引擎重写设计（可靠性 / 重试 / 批次 / DeepSeek 集成）

> 取代旧的 `translation-retry-redesign.md`。整合：专家建议 + 多项目调研
> (`translation-quality-survey.md`) + **已核实的 DeepSeek V4 API 事实**。
> 状态：**待你 review / 确认**。确认后按"实施顺序"分阶段实现 + 每阶段测试。

---

## 0. 核心原则（相对旧设计的升级）

旧设计 = "快速失败 + 全局轮次减半"。**升级为**：

> **失败分类(typed) → 传输层/语义层两层重试 → item 级部分接受 → 失败优先队列(各自几何收缩) → 三态终态**

不再把"网络故障 / 输出截断 / 格式错 / 质量错"混成一种失败(否则会无谓拆分 + 重复计费)。

---

## 1. 已核实的 DeepSeek V4 事实（驱动决策表）

| 事实 | 结论 | 来源 |
|---|---|---|
| `finish_reason` 取值 | `stop`/`length`/`content_filter`/`tool_calls`/`insufficient_system_resource` | API ref ✅ |
| 错误码 | **中止** 400/401/402/422;**重试** 429/500/503 | error_codes ✅ |
| JSON mode | 支持 `response_format={"type":"json_object"}`,**但 prompt 里必须出现 "JSON" 字样**,否则可能吐无尽空白→截断 | API ref ✅ |
| penalty | thinking 模式下 temperature/top_p/**presence/frequency penalty 全部无效**;非 thinking 未文档化 | thinking_mode ✅ |
| 我们的设置 | **thinking 默认关**(translate_online 显式 disabled)→ temperature/top_p **对我们有效**;penalty 可删(0.0 无意义) | 代码确认 |
| temperature | 官方翻译推荐 **1.3**;"temp/top_p 只调一个"是 OpenAI 惯例**非 DeepSeek 规则** | parameter_settings ✅ |
| KV cache | **自动开启**,相同 prefix 命中;`usage` 返回 `prompt_cache_hit_tokens`/`miss_tokens`;命中 input **便宜 ~50x** | kv_cache + pricing ✅ |
| 限制 | 上下文 **1M(1,048,576)**,最大输出 **384K**;默认 max_tokens 未文档化→**必须显式设** | pricing ✅ |
| Retry-After | **官方未文档化,不保证存在**→指数退避+jitter 为主,有 header 才用 | rate_limit ✅ |
| tokenizer | DeepSeek 自带(PreTrainedTokenizerFast, max_len 1048576);但 `usage` 才是权威 | token_usage ✅ |
| 模型 id | `deepseek-chat`/`deepseek-reasoner` **2026/07/24 弃用**;用 `deepseek-v4-flash/pro` | pricing ✅ |

---

## 2. 失败决策表（实现核心）

请求返回后,**先看传输层(HTTP/异常),再看 finish_reason,再看 JSON/对齐,再看 item 级质量**：

| 失败类型 | 行为 |
|---|---|
| **401 / 402** | **立即中止整个任务**(本地化提示;现有逻辑保留) |
| **400 / 422** 参数/格式错 | **中止 + 提示配置问题** |
| **429** | 遵守 Retry-After(若有)否则指数退避+jitter;**AIMD 降并发**;**原 batch 重试**(循环等到冷却真正到期) |
| **timeout / 500 / 503** | 原 batch **快速重试 1 次**(带 jitter);仍失败→进失败队列 |
| **`insufficient_system_resource`** | 同 503:降并发后原 batch 重试 1 次 |
| **`finish_reason=length`**(截断) | **不是 error**:本次输出预算翻倍重试 1 次;仍截断→**拆分 batch** |
| **JSON 空 / 无尽空白** | 原 batch 重试 1 次,prompt 轻微调整(强调 JSON) |
| **JSON / 键 / 对齐失败** | **接受合法 item**;失败 item → repair-reask 1 次 → 仍失败拆分 |
| **占位符丢 / 残留源语言 / 译==原** | **只重试对应 item** |
| **`content_filter`** | 递归拆分定位;最终标记 **needs_review** |

**两层重试**：
- **传输层**(429/timeout/500/503/insufficient_resource):最多 **1–2 次**有限退避。
- **语义层**(格式/缺行/占位符/译未变):**repair-reask 1 次**(带明确错误反馈),再几何收缩/拆分。

---

## 3. 控制流（目标）

```
初始分批(input_batch_tokens, 且 ≤ 硬上限 N 条)
  └→ 每 batch 独立处理(线程池):
       传输层有限重试(decision table)
       → 读 finish_reason(length→扩预算/拆分)
       → 解析 JSON(空白/截断→重试)
       → 逐 item 校验(段数/键/对齐/占位符/残留源语言/译==原)
       → 原子提交"通过"的 item(translated_status=True)  ← 部分接受
       → 失败 item 进【失败优先队列】
            └→ repair-reask 1 次(带错误信息, 清除旧译文上下文/保留源上下文)
               → 几何收缩 batch: max(1, round(n * pow(target/n, 0.25)))  ← 借 LinguaGacha
               → 递归拆分(限 max_split_depth, 如 4)
               → 单 item 有限重试
               → 仍失败 → needs_review / failed
状态文件(src_split + result_split 的 translated_status)= 唯一事实来源
failed.json → 仅诊断产物(不再作为调度源)
```

**三态终态**(替代旧的"last_try 强制接受非空")：
- `translated` — 通过全部校验。
- `needs_review` — 有合法输出但未过质量检查(占位符/残留源语言/content_filter 等);**保留为候选译文,但不算成功**。
- `failed` — 无可用输出;保留源文。

**失败优先队列 vs 旧的"全局轮次减半"**：每个失败 batch **独立收缩**,成功 item 立即提交,正常 batch 不等本轮结束 → 更快、更省。

---

## 4. DeepSeek 集成改造

| 项 | 现状 | 改为 |
|---|---|---|
| finish_reason | **不读** | 读 `choices[0].finish_reason`,接决策表(length/content_filter/insufficient_resource) |
| JSON mode | 不用 | 文档翻译启用 `response_format={"type":"json_object"}`(prompt 已含 "json" 字样;需校验现有 prompt 都提到 JSON) |
| penalty | 发送 presence/frequency=0.0 | **删除**(thinking-off 未文档化、thinking-on 无效;0.0 无意义) |
| temperature/top_p | 每模式同发两者 | 保留(DeepSeek 不禁止同发);默认 precise(temp0.1,严格 JSON 更稳);**A/B 0.1/0.6/1.0/1.3** |
| max_tokens(输出) | 已加 `max_completion_tokens`(api_config) | 保留并**动态计算**(2K–16K,按 batch 估算,不设 384K) |
| max_token(输入批次) | 4096(已 per-model) | 保留;加 **硬上限 N 条**(如 64)防大量短文本挤一批;A/B 4K/8K |
| KV cache | 不读 | 读 `usage.prompt_cache_hit_tokens/miss_tokens`,**遥测+按命中算成本**;**prompt 静态前缀(system+术语+指令)放最前**,动态原文/签名放后,最大化命中 |
| Retry-After | cooldown 只睡 30s 一次 | **循环等到冷却真正到期**;Retry-After 有则用,无则指数退避+jitter |
| tokenizer | 固定 cl100k_base 近似 | 拆三配置(见下);`usage` 为权威成本 |

**Token 三配置**(替代单一 max_token 混用)：
```
context_window_tokens = 1_048_576   # 模型上限(参考,不直接用)
input_batch_tokens    = 4096 / 8192 # 每请求源文预算(A/B)
max_output_tokens     = dynamic     # 按 batch 估算, 限 2K–16K
```

---

## 5. 上下文策略（当前多线程下被禁用,浪费了长上下文）

现状：`base_translator.py` 多线程时直接 `current_previous=""`(因完成顺序不确定)。

改为(任选/分阶段)：
- **同一文档/场景内顺序翻译,多个文件之间并行**(借 llm-subtrans:顺序利于前文摘要;并行=质量换速度)。
- 或**预构建确定性上下文窗口**(不依赖线程完成顺序)。
- 上下文只放:**前 2–8 条 + 场景摘要 + 角色信息 + 命中术语**;不要整篇重复塞每个 batch(长上下文研究:无选择扩上下文反降质量)。
- **重试时清除可能误导的旧译文上下文,保留源文上下文**。

---

## 6. 保留不动

- ✅ item 级 `translated_status` + continue_mode(断点续传)——已是正确基座。
- ✅ 成功才写、失败不污染(`process_translation_results` 已是)。
- ✅ AIMD 自适应限流(`_AdaptiveLimiter`)、per-model RPM/thread/max_retries、致命错误退出、原子写+1.5s flush。
- ✅ 占位符掩码(`placeholder_mask.py`)、text_rules、去重、coverage。
- ✅ 现有校验(段数/对齐/占位符/长度比/字幕行长/术语)——再补"残留源语言""译==原 Jaccard"两项。

---

## 7. 实施顺序（分阶段,每阶段测试）

1. **删 1 小时嵌套重试循环**(`llm_wrapper.py:87` `max_retry_time=3600`;`base_translator` 的 process_segment/process_failed_segment 同款)。
2. **引入 typed failure + 读 finish_reason + DeepSeek JSON mode**;删 penalty。
3. **修传输重试**:最多 2 次;**Retry-After 循环等满**(`online_translation._cooldown_wait` 现在只睡 30s 一次)。
4. **item 部分接受 + 失败批次几何收缩 + 失败优先队列**(核心);三态终态(去掉 last_try 强制接受)。
5. **failed.json 降级为诊断**(状态文件为唯一事实源)。
6. **上下文窗口 + KV cache 遥测 + 定向 QA/refinement**(只修 QA 标记项,不无条件整篇二译——TEaR 研究)。

---

## 8. 每阶段测试计划

- 单测:mock LLM 注入各类失败(429/timeout/length/JSON 错/缺行/占位符丢)→ 断言决策表行为、部分接受、收缩、三态。
- 集成:用真实 DeepSeek 跑一个文档 + 一个字幕,看重试/计费/缓存命中遥测。
- 回归:全套 test_qt_app/i18n/web_sessions/server_mode 绿;两端冒烟。
- 对比:同文档 重写前 vs 后 的"请求数 / 重复计费 / 最终未译率 / 耗时"。

---

## 9. 待你确认的开关

1. **JSON mode**:文档翻译启用 `response_format=json_object`?(需确认现有 13 份 prompt 都提到 "JSON";字幕/游戏格式是否也启用?)
2. **temperature**:默认保持 precise(0.1) 还是改官方推荐 1.3?(建议:保持 0.1 严格 JSON 更稳,提供 A/B)
3. **penalty**:确认删除 presence/frequency penalty?(建议:删)
4. **上下文**:先做"文档内顺序+文件间并行",还是先只做"确定性窗口"?(建议:先确定性窗口,风险小)
5. **几何收缩参数**:沿用 LinguaGacha `pow(target/n,0.25)` + max_split_depth=4?
6. **needs_review 终态**:Web/Qt 历史/校对页要不要展示"待复核"状态?(建议:要,接现有历史)
7. **KV cache 遥测**:成本卡(`pricing.py`)是否要显示 缓存命中率/省下的钱?
