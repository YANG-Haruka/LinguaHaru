# 翻译 Retry / 批次逻辑重写 —— 设计文档（参考 AiNiee）

> 目的：把当前"单请求内长时间退避重试 + 固定批次"的方案，改成 AiNiee 式
> "**失败快速放弃 + 外层轮次重收集未译行 + 批次逐轮减半**"。
> 状态：**待你确认**。确认后再写代码。

---

## 0. 结论先行

你的架构**已经有 AiNiee 的骨架**——`item.translated_status` 状态位、`continue_mode`
跳过已译、外层 `retranslate_failed_content` 重试循环都在。所以**不需要全部重写**，只需
改 **3 个点**。下面逐一说明现状、AiNiee 做法、改法。

---

## 1. 当前架构（`core/engine/base_translator.py` + `text_separator.py` + `translation_checker.py`）

**主流程** `_process_impl`（base_translator:1129）：
1. 抽取 → 去重(`deduplicate_translation_content`) → 切分(`split_text_by_token_limit`，单段 ≤256 token) → 每段写 `translated_status=False`。
2. `translate_content(progress_callback)`：首遍翻译。
3. 外层重试循环（1258）：
   ```python
   retry_count = 0
   while retry_count < self.max_retries and self.translated_failed:
       is_last_try = (retry_count == self.max_retries - 1)
       self.translated_failed = self.retranslate_failed_content(
           retry_count, self.max_retries, progress_callback, last_try=is_last_try)
       retry_count += 1
   ```
4. `check_and_sort_translations` → `restore_*` → 写回。

**批次** `stream_segment_json(max_token)`（text_separator:207）：贪心累加 item 到 `max_token - prompt开销`，`continue_mode` 跳过 `translated_status=True`。**已是 item 级、按 token、只挑未译**——和 AiNiee 一致。

**单请求处理** `process_segment`（base_translator:365）—— ⚠️**问题所在**：
```python
max_retry_time = 3600   # 1 小时
while True:
    self.check_for_stop()
    retry_count += 1
    try: ... 调 LLM + process_translation_results 校验 ...
    except: backoff = min(2**min(retry_count-1,6), 60)  # 1,2,4,…,60s
            sleep(backoff)   # 直到成功或满 1 小时
```
**单个批次最长能在请求内退避重试 1 小时**。

**校验** `process_translation_results`（translation_checker:161）：已有段数/对齐检查，校验通过写 `translated_status=True`，失败写 `failed_json_path`。

**重试** `retranslate_failed_content`（base_translator:545）：
- 从 `failed_json_path` 重新 `stream_segment_json(max_token)` —— ⚠️**批次大小不变**（仍是 `max_token`）。
- 仅 `last_try=True`（最后一轮）才**直接拆成逐行**。
- 内部 `process_failed_segment` 同样有 1 小时退避重试。

---

## 2. AiNiee 的做法（已读其源码，详见对话）

| 维度 | AiNiee |
|---|---|
| 完成单元 | item，带 `translation_status`，只重收集 `UNTRANSLATED` |
| **单请求重试** | **无**。失败不写缓存、直接返回；成功才原子写 |
| **外层重试** | `round_limit=10` 轮；**每轮 `tokens_limit/lines_limit` 减半**（`max(1,n/2)`），逐步逼近逐行 |
| 失败兜底 | 耗尽轮次仍未译 → 留空（不强标） |
| 限流 | 只有 `60/rpm` 最小间隔 + 单请求 token 硬上限；**无 429 退避**；超时(120s)放弃交下一轮 |
| 默认 | token 模式 1024/请求；线程数按 rpm 插值 |

---

## 3. 差距 & 改动（3 个点）

### 改动 ① 单请求"快速失败"，删掉 1 小时退避循环 ⭐核心
**现状**：`process_segment` / `process_failed_segment` 的 `while True` + `max_retry_time=3600` + 指数退避。一个卡住的批次能拖一小时，且失败整批反复重发。
**改法**：单请求**最多尝试 1 次**（或对"网络瞬时错误"额外 1 次快速重试，无长退避）。失败→标记该批次 item 仍 `translated_status=False`、写不进结果、**立即返回**。把可靠性交给外层轮次循环。
- 致命错误（402 余额/401 密钥，`HardApiError`）仍立即整体退出（保留现有逻辑，见 [[error-handling-history-resume]]）。
- 网络超时/5xx：不在请求内死等；记为本轮失败，下一轮（更小批次）再试。
- 删除 `max_retry_time=3600`、`backoff=2**...` 那套。

### 改动 ② 外层轮次"批次减半" ⭐核心
**现状**：每轮 `retranslate_failed_content` 用**同样的 `max_token`**，只有最后一轮跳到逐行。
**改法**：每进入新一轮，`effective_max_token = max(MIN_TOKEN, effective_max_token // 2)`（AiNiee 式），传给 `stream_segment_json`。这样 4096→2048→1024→…→逐行**平滑收敛**，而不是"前 N 轮原样、最后一轮突然逐行"。
- `MIN_TOKEN` 取一个保证单行能放下的下限（如 0 = 不限单行，或 256）。
- 删除 `last_try` 的特判逐行分支（被减半收敛取代；或保留为最后兜底）。

### 改动 ③ 失败重收集：用 `translated_status` 而非独立 failed 文件（可选）
**现状**：失败写 `failed_json_path`，重试读它。
**改法（可选，更接近 AiNiee）**：直接对 `src_split_json` 重新 `stream_segment_json(continue_mode=True)`——它已会跳过 `translated_status=True`，自然只收集未译行。可去掉 `failed_json_path` 这条冗余链路。
- ⚠️风险：`failed_json_path` 现在也用于"校验失败但非未译"的边界情况，需核对 `process_translation_results` 里写 failed 的全部分支，确保都对应 `translated_status=False`。**保守做法：先不动这条，只做 ①②**。

---

## 4. 新主循环（目标形态）

```python
# _process_impl 内，替换现有首遍+重试段
effective_max_token = self.max_token
round_limit = self.max_retries          # 复用现有配置语义
self.translate_content(progress_callback, max_token=effective_max_token)
rnd = 0
while rnd < round_limit:
    remaining = count_untranslated(self.src_split_json_path)   # status=False 计数
    if remaining == 0:
        break
    effective_max_token = max(MIN_TOKEN, effective_max_token // 2)   # 减半
    self.retranslate_failed_content(rnd, round_limit, progress_callback,
                                    max_token=effective_max_token)
    rnd += 1
# 耗尽仍有未译 → check_and_sort 标缺失（现有逻辑）
```
- `translate_content` / `retranslate_failed_content` / `stream_segment_json` 增加 `max_token` 形参（覆盖 `self.max_token`）。
- 单请求处理函数删退避循环，失败即返回。

---

## 5. 保留不动

- ✅ item 级 `translated_status` + `continue_mode`（断点续传，已是 AiNiee 模型）。
- ✅ 成功才写结果、失败不污染（`process_translation_results` 已是这逻辑）。
- ✅ 现有校验（段数/对齐/占位符）—— 后续可按需补 AiNiee 的"译≠原(Jaccard≥0.85)""残留源语言"两项（另议）。
- ✅ 进度计数(item 级)、缓存 1.5s flush、原子写、致命错误退出、AIMD 并发限流(`_AdaptiveLimiter`，见 [[translation-concurrency]])——**注意**：AiNiee 无 429 退避，但我们已有 AIMD，比 AiNiee 更强，**保留**。
- ✅ Web/Qt 两端共用此后端，无需改前端。

---

## 6. 风险 & 验证

- 风险：删退避后，瞬时网络抖动会让该批次落到下一轮——但下一轮批次更小、且 AIMD 仍在压并发，整体更快收敛、不会卡死。
- 风险：批次减半 + 单段已 ≤256 token，注意 `effective_max_token` 低于单段 token 时 `stream_segment_json` 要能"一段一请求"不报错（现有 `segment_available_tokens<=0` 已有兜底）。
- 验证：(1) 单测 mock LLM 让前 2 轮部分失败，断言轮次减半 + 最终全译/收敛；(2) 用 hhd800 字幕实跑对比 retry 行为与耗时；(3) 全套测试 + 两端冒烟。

---

## 7. 待你确认的开关

1. **改动③（去 failed_json，改用 status 重收集）做不做？** 建议：先只做 ①②（低风险），③ 以后再说。
2. **round_limit 默认**：复用现有 `max_retries`(per-model，Flash=…) 还是固定 10(AiNiee)？建议复用现有。
3. **单请求是否保留 1 次快速重试**（仅瞬时网络错，无长退避）还是 0 次纯失败快放？建议保留 1 次快速重试。
4. **MIN_TOKEN 下限**：0(每段单请求) 还是 256？建议 0（逐行兜底）。
