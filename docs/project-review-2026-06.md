# LinguaHaru 项目审查 + 可借鉴功能（2026-06-20）

> 两部分：A. 内部代码审查（欠缺/可优化，按 P0/P1/P2）；B. 对标 GitHub 翻译项目，挑可借鉴的功能/逻辑。
> 说明：本会话已对核心引擎/管线做过深审并修复多项；故内部审查以 **P1/P2** 为主，未发现新的 P0 数据丢失级 bug。外部对标的并行搜索代理本轮撞到 API 额度（8:50 Tokyo 重置），下方借鉴清单综合自本会话已完成的《前后处理调研》(docs/translation-pre-post-processing.md) + 既有认知，额度恢复后可再补新搜。

---

## Part A — 内部代码审查

### P1（鲁棒性 / 质量，值得做）

1. **API 错误分类靠字符串匹配，非 SDK 类型/状态码** — `core/llm/online_translation.py:651-679`
   - 现状：`"429" in error_msg` / `"500" in error_msg` 等子串判断。脆：消息里偶含数字会误判；不同 provider 文案不一。
   - 建议：优先用 OpenAI SDK 的 `openai.RateLimitError` / `openai.APIStatusError.status_code` / `openai.APITimeoutError` 类型分支，字符串作兜底。

2. **QA 警告（qa.json）只写盘、两端 UI 都不展示** — `core/engine/base_translator._write_qa_report` 写 `qa.json`，但 webapp/qt 都没渲染。
   - 现状：占位符/长度比/术语/字幕 CPS/行数 等警告用户看不到（coverage 有展示，QA 没有）。
   - 建议：在两端结果区加一个折叠的「质量提示」面板（数据已现成，纯前端增量，低风险）。**ROI 高。**

3. **Web 实时 VAD 仍是能量 worklet，Qt 已是神经 TEN-VAD** — `webapp/static/vad-worklet.js` vs `qt_app/live_page.py`。
   - 现状：Web 端抗噪明显弱于 Qt；两端 parity 差距。
   - 建议：Web 上 Silero(onnxruntime-web/WASM) 或把 PCM 发后端用 TEN-VAD 复核（需真麦克风测）。

4. **无跨运行/跨文档 翻译记忆(TM)** — 仅 BabelDOC 对 PDF 有 SQLite 段缓存；通用文档/字幕路径每次从头译。
   - 影响：相同句子跨文件/重跑重复消耗 + 译文可能不一致。
   - 建议：见 Part B #1（可作为最高价值新功能）。

5. **rpm_limit / 部分 config 改了需重启**（无热重载）— `translation-concurrency` 记录。低频但易踩。

6. **temp/result 是 session 级非 task 级** — 同 session 同名文件并发可能互相覆盖（边缘）。`webapp/sessions.py`。

7. **Excel 管线 119 处 except** — `core/pipelines/excel_translation_pipeline.py`。可能掩盖静默失败；建议抽样核对关键 except 是否吞掉了应上报的错误。

### P2（打磨）

8. **复杂格式写回边角**：Word 页眉页脚 SDT 写回、HTML 内联 `<code>`、EPUB 内联图 alt、Excel 跨 sheet 公式重命名 — 已知限制。
9. **实时 partial 仍是增长窗口 O(n²)** — 长句每次重解码；可改纯增量 local-agreement（与现 stable-prefix 一起重构）。
10. **字幕 QA 只警告不自动重切** — CPS/行宽超标只提示，未自动 merge/split cue（见 Part B #6）。
11. **frozen 下自定义接口写只读 bundle** — 源码运行无影响。

---

## Part B — 可借鉴的 GitHub 功能（按价值排序）

### 高价值（建议优先）

1. **翻译记忆 TM + 模糊复用** ★最高
   - 谁：Weblate / OmegaT / 各 CAT 工具；`rapidfuzz` 做模糊匹配。
   - 价值：跨文件/重跑复用已译句（100% 直接用、75-99% 作上下文、<75 重译），省 token + 全局一致。LinguaHaru 现在只有 PDF(BabelDOC) 有缓存。
   - 做法：`data/tm.sqlite`（key=源文+语向+model），译前查 TM 命中直接填，未命中才发 LLM；支持 TMX/CSV 导入导出。中等工作量，ROI 极高。

2. **字幕时间轴同步（import 不同步的字幕）**
   - 谁：`ffsubsync`(FFT 卷积对齐) / `alass`。
   - 价值：用户导入与视频不同步的 .srt，自动对齐后再翻；当前完全没有。
   - 做法：可选插件（pip `ffsubsync`），在字幕/视频页加「同步时间轴」。中等。

3. **TTS 配音到视频（dubbing）**
   - 谁：VideoLingo（整套：STT→译→TTS→对齐→混音回视频）。
   - 价值：你已有 TTS 朗读 + 视频字幕，差「生成配音音轨并混回视频」。受众大（短视频/教程本地化）。
   - 做法：edge-tts 合成每条 cue → 按 cue 时长拉伸/对齐 → ffmpeg 混音。较大但模块清晰。

4. **增量重译（按源 diff 只译改动段）**
   - 谁：doctranslate / TM 思路。
   - 价值：文档改几句后重跑，只译变化的段落（配合 TM）。对迭代文档极省。
   - 做法：按段落 hash 比对上次源，命中 TM 跳过。配合 #1。中等。

5. **质量估计(QE)surfaced + 反思/最佳N**
   - 谁：translation-agent(Andrew Ng,三步 reflect/refine)、MAPS、COMET-Kiwi(reference-free QE)。
   - 价值：你有 polish 二阶段；可加「困难段触发反思重译」+ 对 flagged 段跑 COMET-Kiwi 打分展示。做成翻译模式。中等（QE 需 GPU/模型，可选）。

6. **字幕 CPS 感知自动重切/合并 cue**
   - 谁：Subtitle Edit（Merge Short / Split Long / Min Gap / Duration limits）、stable-ts regroup。
   - 价值：你已有 CPS/行宽 QA「警告」，再进一步「自动修」：超 CPS 的 cue 延长/拆分，过短合并。质量直接可见。中等。

7. **把 QA 警告展示到前端**（同 A#2）—— 纯前端，最低成本高收益。

### 中价值

8. **多引擎兜底链** — 引擎 A 失败/低置信 → 自动转 B。你已有多接口，差「链式回退」编排。
9. **Web 神经 VAD(Silero) + mask-k 降闪烁**（同 A#3）。
10. **文件夹/批量 + watch 监视模式 + CLI** — 自动化/批处理；现仅 GUI+Web 多文件。`watchdog` + 一个 `cli.py`。
11. **说话人分离标签(可选)** — 之前移除过；多人对话/访谈字幕有用，可作开关重加（cam++ 声纹，已有过实现可复活）。
12. **词级/卡拉OK字幕时间轴** — Qwen3-ForcedAligner/whisperX 对齐（你之前决定 ForcedAligner 不做；此为同类，按需）。
13. **术语表 TMX/CSV 导入导出 + 项目级术语库** — 现有 glossary 可加标准格式互通。
14. **Prompt 预设/档案（按项目/领域）** — 保存常用风格+术语+模式组合。

### 低价值 / 已较强
- 浏览器扩展 / 公共 API 面（看产品方向）。
- 你已强于多数项目处：占位符保护+round-trip 校验、三终态重试、stable-prefix 实时、双端、per-model STT 参数、LaMa+竖排图片、coverage、多用户。

---

## 建议的下一步（若继续）
- **立刻可做、低风险高收益**：A#2/B#7（QA 警告上前端）、B#1（翻译记忆 TM）。
- **受众大、模块清晰**：B#2（字幕同步）、B#3（TTS 配音）。
- **需真机/素材**：A#3 Web 神经 VAD、发布测试语料。
- 外部对标的新一轮 GitHub 深搜可在 API 额度恢复(8:50 Tokyo)后补做，进一步细化 Part B。
