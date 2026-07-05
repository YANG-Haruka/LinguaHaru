# LinguaHaru 项目审查 + 可借鉴功能（2026-06-20）

> 两部分：A. 内部代码审查（欠缺/可优化，按 P0/P1/P2）；B. 对标 GitHub 翻译项目，挑可借鉴的功能/逻辑。
> 说明：本会话已对核心引擎/管线做过深审并修复多项；故内部审查以 **P1/P2** 为主，未发现新的 P0 数据丢失级 bug。Part B 已用 GitHub 深搜代理（覆盖 18+ 项目）刷新，含关键设计坑。
>
> ⭐**一个贯穿全文的核实**：LinguaHaru 现在 **已经算出 Whisper 词级时间戳**（`video_translation_pipeline.py` `word_timestamps=True`）**但用完即扔**；字幕 CPS/行宽检查（`translation_qa.py`）也**只警告不修**。下面好几项之所以"便宜"，正是因为原始信号已经在管线里了。

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

## Part B — 可借鉴的 GitHub 功能（GitHub 深搜刷新，18+ 项目）

> 关键元结论：**持久化跨运行缓存/TM、自动引擎兜底、质量打分、TMX 术语、prompt 预设** 在开源生态里大多**缺位**——多数"参考"项目其实没真做（pot-app 的"兜底"是并排显示;immersive-translate 的缓存忽略参数会过期;AiNiee 的"档案"是单一激活 prompt）。所以这些是**差异化点**，不是抄作业。

### TIER 1 — 高收益 / 小到中等成本 / 明确缺失（先做这些）

1. **持久化跨运行缓存 + 精确 TM**（单一 SQLite 同时搞定缓存/增量/去重）★最高
   - 谁：pdf2zh `cache.py`（key 设计的正面教材）/ immersive-translate（反面教材）。URL: github.com/Byaidu/PDFMathTranslate
   - 价值：key=`(src_hash, 目标语, engine, params_sig)` → 重跑只译**变动段**（免费增量重译）+ 跨文档去重 + 兼当崩溃续传。当前通用路径每次从头译。
   - **关键设计坑（pdf2zh 对、immersive 错）**：把**所有影响输出的变量**都塞进 params_sig（model、temperature/模式、**prompt 版本**、**glossary 哈希**、masking 开关），用递归 sorted-key json.dumps；否则结果过期+缓存膨胀。
   - **尖角**：SQLite 必须 **WAL + busy timeout**（否则线程池下 `database is locked`）；哈希**归一化语义段**而非 API chunk（改 chunk 预算会重切段、爆掉所有哈希）；哈希 **mask 前**源文，让 masking 配置随 params_sig 走。
   - **精确 TM 子项**：哈希 **mask 后** 源（`Hello {name}`/`Hello {user}` 会碰撞）→ 重复句 0 token 0 延迟；文档内相同段自动传播。
   - 成本：**小**（~60-100 行 stdlib sqlite3，仿 `translation_history.py`）。光 #1 就能在"缓存+增量"上领先几乎所有开源同类。

2. **廉价质量估计(QE)heuristics → 展示为"置信度" + 自动入 needs_review**
   - 谁：Weblate 质检引擎 / AiNiee 语言校验 / llm-subtrans 校验。
   - 价值：5 个近零成本检查（全 CPU）：未译/copy、**输出语种 ID 不符**(langdetect≠目标)、长度比异常、**n-gram 重复**(正是你近期打的复读 garbage)、占位符/结构完整。结果灌进现有 `needs_review` + 前端展示。**没有就只有 coverage、没质量信号。**
   - 成本：**小**（无 ML，叠现有 coverage/placeholder/needs_review）。

3. **术语表 CSV/TSV 导入导出 + forbidden/case-sensitive 标志**
   - 谁：OmegaT(3 列 TSV)、Weblate(forbidden/read-only/terminology)、memoQ(大小写)。
   - 价值：术语多在表格里，CSV/TSV 导入导出覆盖 ~90% 真实术语 + 可跨项目共享。**forbidden**(禁某译法)、**case_sensitive** 两个新标志既改 prompt 又能事后子串校验。
   - 成本：**小**（~半天，落 `data/glossary/`，默认 OmegaT 3 列 TSV）。TBX 是中等加分项。

4. **Quick Translate 上的自定义 prompt "Actions"**
   - 谁：openai-translator（Polish/Summarize/Explain/自定义）。URL: github.com/openai-translator/openai-translator
   - 价值：翻译页之外的通用动作，每个 `{name,icon,rolePrompt,commandPrompt}` + `${text}/${sourceLang}/${targetLang}`，渲染成按钮——把翻译器变成迷你文本工具，完全复用 `translate_text_simple`。
   - 成本：**小**。

5. **字幕时间轴 FFT 重同步（导入不同步字幕）**
   - 谁：ffsubsync（10ms 二值化 + `FFT(sub)×FFT(reversed ref)` 求全局偏移 + 帧率比黄金分割搜索）。URL: github.com/smacke/ffsubsync
   - 价值：当前**零**重同步；你**已有两路信号**(TEN-VAD 参考 + cue 时间)，~40 行 numpy/scipy `fftconvolve`，修一整类坏导入。
   - 成本：**小**。

### TIER 2 — 强价值 / 中等成本

6. **CPS/行宽 警告 → 自动 merge/split 修复**（用已算出的词级时间戳）
   - 谁：python-lyrics-transcriber `segment_resizer`、stable-ts regroup、VideoLingo `merge_rows`、srt_equalizer。
   - 价值：`translation_qa.py` 已有 per-lang CPS/cell 表 + 知道哪条超标，**就差会修**：评分选切点(句末>从句>逗号>空格 + 均衡两半 + 首行~70%) + 用**真词时间戳**给两半重分时间(你已有却扔了) + 合并闪烁短 cue + 强制 min(~1s)/max(~6s)/最小间隔。**字幕路径最大功能缺口。**
   - 成本：**中**。

7. **强制对齐拿引擎无关的词级时间戳（keystone）**
   - 谁：ctc-forced-aligner（`mms-300m-1130` 多语 MMS + 罗马化，**对 CJK/RU 比 whisperX 的 per-lang wav2vec2 更好**）、whisperX 技术。URL: github.com/MahmoudAshraf97/ctc-forced-aligner
   - 价值：最高杠杆使能项——解锁 #6 精确切分、#10 卡拉OK、收紧间隔；**引擎无关**(SenseVoice/Qwen/anime 之后都能用，不止 Whisper)。
   - 成本：**中**。

8. **Web 端神经 Silero VAD（替换能量 worklet）**
   - 谁：@ricky0123/vad-web(AudioWorklet 跑 Silero v5 ONNX-Web)。URL: github.com/ricky0123/vad
   - 价值：Web 能量 VAD 在音乐/风扇/键盘、尤其**系统声音捕获**模式下误触发；Qt 已神经 TEN-VAD，这是补 Web parity，保留现有 pre-roll。
   - 成本：**小–中**。

9. **反思/批评回路（4 维结构化清单）**
   - 谁：translation-agent(accuracy/fluency/style/terminology 4 维批评→编辑)。URL: github.com/andrewyng/translation-agent
   - 价值：你的 polish 是单发；结构化批评能抓漏译/术语漂移。复用现有二阶段槽 + chunking。**门控在"深度/质量"模式后，默认单发。**
   - 成本：**小**(纯 prompt)。

10. **全文术语一致性 + 前向摘要**
    - 谁：DelTA、TransAgents、VideoLingo 摘要→glossary。
    - 价值：长文/书/字幕第一可见缺陷=人名忽左忽右；维护「专名记录(源→定译)」+ 事后检出分歧重译 + 滚动「双语摘要」前置每 chunk。你现在只注入**命中**术语 + 只喂相邻行(实时)。
    - 成本：**中**（与 #7 共享一个状态对象，一起做）。

11. **语义/声学 end-of-turn 端点检测（自适应静音超时）**
    - 谁：pipecat smart-turn-v3(8M 参数, ~8MB int8 ONNX, ~12ms CPU)。
    - 价值：现两端固定静音阈值 → "我觉得…我们"被切错；这是实时字幕最大**质量**赢。⚠️验证 CJK 覆盖，按源语言 opt-in。
    - 成本：**中**。

12. **TTS 配音到视频（全新能力）**
    - 谁：open-dubbing(Softcatalà)、VideoLingo。URL: github.com/Softcatala/open-dubbing
    - 价值：你有 ffmpeg+STT+翻译三块,差配音。核心=时长适配级联(估 TTS 长→合并相邻 cue→LLM 缩短→ffmpeg `atempo` 保调,上限~1.3-1.4×,超了宁可溢出不要花栗鼠音)。**差异化：别人混音时删原声,你可以 duck(压低)而非删。**
    - 成本：**大**(级联本身~30 行,全管线+TTS 音色依赖是大头)。

### TIER 3 — 不错的小赢 / 受众较窄
- **卡拉OK/词级字幕(ASS `\k`/`\kf`)** — 有 #7 后基本免费(纯序列化)。lyrics-transcriber/stable-ts。**小**。
- **真·定位双语字幕(ASS \an8 顶 / \an2 底, 各自字体)+ 烧录模板(ffmpeg force_style + h264_nvenc)** — 现在是 `\N` 堆叠。VideoLingo。**小**。
- **alass 分段惩罚 DP**(广告插入/导演剪辑的中段漂移,FFT 修不了) — 作 #5 的升级回退。**中**。
- **前缀/术语偏置解码(实时稳定)** — whisper 每窗用已确认前缀+静态 glossary 作 initial_prompt(锁定文本不再闪 + 术语进 STT)。SimulStreaming。Whisper 限定。**小–中**。
- **Best-of-N + 判官融合(非选择)** — N=3 不同温度采样后融合成一条,门控在 Quick-Translate/标题/字幕行控成本。Hunyuan-MT-Chimera。**中**。
- **多引擎兜底链 / 并排对比** — 失败/超时/限流自动转下一引擎(Weblate 分数排序;注意**无开源 LLM 工具真做了自动 failover**=差异化);多 key round-robin 是小赢。**中**。
- **JSON 声明式自定义 HTTP 引擎**(用户填 request/response 模板接任意 API,如 Hunyuan-MT) — Ebook-Translator-Calibre。**中**。
- **离线兜底引擎(Argos/LibreTranslate)** — 无 key/离线/欠费时最后手段,质量低一档,标"降质"。**小码中胶水**。
- **OpenAI 异步 Batch API 模式** — 大文档非交互省 ~50% 成本。bilingual_book_maker。**中**。
- **双语显示样式预设 / 增量 watch+CLI / prompt 档案** — 见原列表。

### 漫画/图片轨（独立路线，仅当聚焦漫画时）
气泡检测(comic-translate RT-DETR / BallonsTranslator YOLO)=使能项 → 气泡内适配文字+自动字号+换行(manga2eng) → **手动 typeset 编辑器**(BallonsTranslator,QGraphicsView WYSIWYG,你已是 PySide6 可直接学,最大 UX 缺口)。免编辑器快赢：mask_dilation + anime-finetune LaMa、CBZ/CBR/PDF 解包章节批处理、webtoon 长条按空白切、RTL 阅读序喂 LLM。

### 明确**不**建议
- pot-app JS 插件沙箱(过度工程;只取它的 `needs` 配置 schema=#自定义HTTP)。
- **COMET-Kiwi 作默认**(模型**gated + 非商用 CC-BY-NC-SA + ~2.3GB**;商用是雷)。要真 QE 用 LaBSE(Apache)或 LLM-as-judge;且只作相对"置信度"标底部异常,别硬卡 0.7。Tier-1 的 #2 启发式已拿 80% 价值。
- 说话人分离(你已删 c18f21c;要回加只借 whisperX 的 longest-overlap 分配 + 用 NeMo Sortformer 避开 HF gating)。
- stable-ts DTW 词时间戳(仅 Whisper,劣于 CTC #7)、end-to-end S2ST(重构非借鉴)。

---

## 建议的下一步（若继续）
- **小成本、信号已存在**：B-Tier1 #1(缓存/TM)、#2(QE→needs_review)、#5(字幕 FFT 同步)、A#2/B#4(把 QA/QE 警告上前端)。
- **keystone 解锁链**：B#7(强制对齐)→ #6(自动 merge/split)、#10/卡拉OK。
- **Web parity**：B#8(神经 VAD)、#11(smart-turn)。
- **大但受众广**：B#10(全文一致性)、#12(TTS 配音)。
- **需真机/素材**：发布测试语料。
- ⚠️ **成本警示**：#2(LLM 变体)/#9/#10/best-of-N 会成倍调 DeepSeek → 一律门控在"深度/质量"模式，默认单发。

### 主要来源
pdf2zh github.com/Byaidu/PDFMathTranslate · ffsubsync github.com/smacke/ffsubsync · ctc-forced-aligner github.com/MahmoudAshraf97/ctc-forced-aligner · @ricky0123/vad github.com/ricky0123/vad · translation-agent github.com/andrewyng/translation-agent · open-dubbing github.com/Softcatala/open-dubbing · VideoLingo github.com/Huanshere/VideoLingo · pipecat smart-turn-v3 (HF) · openai-translator · llm-subtrans · AiNiee · OmegaT · Weblate · LibreTranslate/Argos · comic-translate · BallonsTranslator
