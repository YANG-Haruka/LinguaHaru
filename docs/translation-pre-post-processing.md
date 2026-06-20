# 不同翻译任务的前/后处理调研（开源社区实践 + LinguaHaru 落地建议）

> 2026-06-20。基于 5 路并行开源调研（字幕/视频 STT、实时语音、文档、图片/OCR/漫画、LLM 通用翻译）。
> 每条尽量给出 **技术 → 为什么(WHY) → 怎么做(HOW/参数/库) → 来源**。文末每个任务都有「与 LinguaHaru 现状对照 + 优先级建议」。
>
> 阅读顺序建议：先看 §0（所有 LLM 翻译通用，影响最大），再按你关心的任务跳读。

---

## §0 通用：围绕 LLM 调用本身的前/后处理（适用所有任务）

LinguaHaru 所有文本最终都走「JSON 批量 segment → DeepSeek → 校验」，所以这一层 ROI 最高。

### 0.1 Prompt 与目标语言锚定
- **角色 + 不解释**：几乎所有专用 MT（AiNiee / Hunyuan-MT）都写死「你是专业译者，逐行翻译、不要解释、保留编号/标记/占位符/换行/转义/代码」。
- **目标语言锚定**：LLM 会"翻译开关失灵"回原文或漂到第三语言。对策：明确目标语言（含方言）；**非中文目标语言统一用英文语言名**填模板（AiNiee 实测对 LLM 兼容性更好）。→ 与 LinguaHaru「13 份 prompt 保留目标语言带偏输出」结论一致 [[translation-prompts-glossary-modes]]。
- 来源：AiNiee `PromptBuilder._replace_language_placeholders`；OpenAI Realtime Prompting Guide "Pin output to a target language"。

### 0.2 批量与 token 削减（AiNiee 重点）
- **编号行而非真 JSON**：`json.dumps` 会破坏原文转义字符；AiNiee 用 `{n}.{line}` 编号行包进 `<textarea>`，回传按 `\n(?=\d+\.)` 切分、按源换行数复原。**弱模型用容错编号行+正则回退；强模型（DeepSeek json_object）可用 schema 化 JSON。**
- **稳定 key**：用全局稳定 ID 作 join key（经得起拆批/重试），不要批内重排序号。
- **匹配门控注入**：术语/角色/禁翻表只注入**当前批命中的行**，无关项不耗 token。
- **prompt 缓存**：AiNiee-Next 给 system 块加 `cache_control:ephemeral`。
- 批大小参考：AiNiee `tokens_limit=1024/lines=10`；AiNiee-Next `1500/20`。
- 来源：AiNiee `ResponseExtractor.py` / `GlossaryHelper.py`；AiNiee-Next `AnthropicRequester.py`。

### 0.3 术语 / 禁翻 / 命名实体一致性
- **只注入命中的术语**（非整表）——多源收敛的强结论；DNT = "映射到自身"的术语条目。
- 跨批一致性两套：① 确定性 `before::after` 替换（CJK 用 `(?<!\p{Script=Latin})` 适配无空格）；② LLM 产 `<terminology>` 对、跨批累积回灌并校验防幻觉（llm-subtrans）。
- 来源：AiNiee `GlossaryHelper`、llm-subtrans `Substitutions.py`。

### 0.4 上下文（并发注意）
- 三范式：**前 N 源行**（AiNiee，顺序无关 → 可并发）/ **抽象滚动摘要**（llm-subtrans，顺序依赖 → 难并发）/ **字面滑窗译文**。
- **本项目并发翻译 → 用「前 N 源行」或一次性预生成的全局术语/角色表**，避免顺序依赖的摘要链。→ 印证 LinguaHaru 现状「多线程下禁用 previous_content」是对的。
- 护栏："参考上文但**不要翻译上文**"。

### 0.5 自检/校验（多数项目都有分层校验器）
AiNiee `ResponseChecker` 按序：拒答检测 → 行数/编号连续 → 空值 → **返回原文检测**（Jaccard≥0.85，CJK 排除）→ **残留未译源脚本检测**（JA/KO/ZH 字符缩减率<0.5）→ 格式 → **占位符存活（始终开）**。
通用 QA 工具箱（建议本项目实现）：
1. **未译检测**：`normalize(src)==normalize(mt)` + 对**输出**跑 langID（短串不可靠 → 降级为脚本检测）。
2. **长度比**：`len(mt)/len(src)` 越界报警，**须按语言对校准**（CJK↔Latin 天然变长）。
3. **占位符多重集 round-trip**：译前抽取占位符多重集、译后相等。→ LinguaHaru 已有 `placeholder_mask.py`，但应**额外加 round-trip 校验**（mask 后仍可能被删）。
4. **JSON schema/key-set/count**：`set(out)==set(in)`；json_object 只保语法不保 schema。

### 0.6 修复策略（按成本升序）
1. **纯语法修复（零 API）**：`json_repair` 修尾逗号/未闭合/前言。
2. **只补缺失 key**：缺 N 条就只重发这 N 条源（最省）。
3. 逐项重试 → 4. 降温重试（0.0–0.3）→ 5. schema-repair prompt（回灌错误）。
- **关键区分**：瞬态失败（截断/空/语法坏）重试有效；**语义失败（拒答/漏译/错译）重试同输入无效**，须改输入（更小批/明确缺失列表/降温/错误反馈）。封顶 ~3 次按失败类型门控。
- 来源：`json_repair`；"retries aren't free"。→ 与 LinguaHaru 刚做的「三终态 + 几何缩批 + 失败优先队列」一致 [[file-processing-bugs]]。

### 0.7 多遍精炼（translate → reflect → refine）
- translation-agent 三步（3× 成本）：初译 → 4 维批评(accuracy/fluency/style/terminology) → 5 维改进。
- **实证（重要）**：二遍精炼 **COMET↑ 但 BLEU/chrF↓**（释义偏离参考但语义更好）；**1 遍captures 大部分增益，勿用 BLEU 门控**；每遍须重锚源文。默认封顶 1 遍。
- 来源：translation-agent；EAMT2024 Iterative Refinement。→ LinguaHaru 的 polish/second_pass 模式属此类。

### 0.8 DeepSeek 专项
- **翻译用 `deepseek-chat` 非 reasoner**：reasoner 静默忽略 temperature/penalties 且 CoT 吃预算；只有 chat 上官方推荐的 **translation temp=1.3** 才生效。**首遍 1.3 求质量，修复/重试遍降 0.0–0.3 求确定。**→ LinguaHaru standard 模式 temp 1.3 正确。
- **json_object**：① `response_format`；② prompt 含字面 "json"；③ 给结构示例；④ max_tokens 给足。只保语法不保 schema，偶发空 content / `finish_reason=="length"` 截断须自查。
- **Context Caching**：只命中 **0 号 token 起的相同前缀**，最小 64 token。前缀按「静态→易变」排序：**system → 术语 → JSON 示例（逐字节固定）→ 每批 segment 置尾**；前部任何改动（甚至术语重排）击穿缓存。命中价低约一个数量级。监控 `prompt_cache_hit_tokens`。

**§0 给 LinguaHaru 的优先级**
- P0：DeepSeek prompt-cache 前缀排序（system→术语→示例→segment）+ 监控命中；匹配门控术语注入；补全后处理校验（返回原文/输出 langID/占位符 round-trip/key-set）。
- P1：智能修复链（json_repair → 只补缺失 key → 降温重试）；错误分类 HARD/SOFT/截断。
- P2：可选 1 遍反思精炼（勿用 BLEU 门控，自评用 COMET-Kiwi 只跑 flagged 段）。

---

## §1 文档翻译（Word/PPT/Excel/PDF/MD/HTML/EPUB）

> LinguaHaru 主业。核心矛盾：**为质量须合并碎片整句送 LLM，为保格式须知道每段译文落回哪个 run/tag**。

### 1.1 PDF 版面保留（BabelDOC / pdf2zh）
- **IL/IR 三段架构**：Frontend(解析成中间表示) → Midend(版面分析/段落重建/公式掩码/术语/翻译/排版) → Backend(渲染回 PDF)。
- **解析**：PyMuPDF(字体/字形 bbox) + forked pdfminer 逐算子(`Tj`→`on_lt_char`)拿逐字符定位。
- **版面分析 DocLayout-YOLO(ONNX)**：`imgsz=1024`，置信度<0.25 丢弃，标签 title/text/figure/table/isolate_formula；用 **R-tree + IoU 变体**给字符分配 layout_id。
- **公式占位符掩码(核心技巧)**：三信号判公式——①字体名正则 `(CM[^R]|.*Mono|.*Math|.*Sym|...)`；②Unicode 类别 `Lm/Mn/Sk/Sm` 或希腊块或 `(cid:`；③版面类别。上下标按**字号方差**(<prev×0.79)判。掩成 `{v0}{v1}`，原字形入并行栈，译后回填。纯 `^[0-9, .]+$` 视为可译。
- **段落重建/阅读顺序**：按垂直位置聚行；断段条件=行距>1.4×行高 / 对齐变化 / **字号方差>21%** / layout_id 变化；跨栏跨页缝合。
- **内联富文本**：相同 font/size/color 聚成 run，不同的发 `<style id='1'>…</style>` 占位标签。
- **扫描件**：`DetectScannedFile` + `--ocr-workaround`(译文下铺白底强制黑字)，>80% 页扫描时自动触发。
- **分块**：每批 ≤200 token / ≤5 段，优先级 `1048576−token_count`(短的先)，tiktoken 计数。
- **temperature=0**（注释明说：随机采样会破坏公式标记）；prompt 列保留 token；返回后**删除不在允许集的占位符**(幻觉清理)。
- **翻译缓存**：SQLite+peewee，`UNIQUE(engine, sorted-params, original_text)`；leaky-bucket 限流 + tenacity 指数退避。
- **自适应排版**：缩放 1.0→0.1(>0.6 步 0.05 否则 0.1)，**先减行距(CJK 1.5)再缩字形**，三级回退(重排→缩放→盒子扩展)；CJK 输出 Noto，量字宽自动换行/收缩。
- **双语输出**：mono + dual(并排/交替页)。
- 来源：BabelDOC https://github.com/funstory-ai/BabelDOC · pdf2zh https://github.com/Byaidu/PDFMathTranslate · DocLayout-YOLO https://github.com/opendatalab/DocLayout-YOLO

### 1.2 编号批格式 + 校验（AiNiee / llm-subtrans，最具参考价值）
- **批格式：编号纯文本而非 JSON**。AiNiee `{n}.text` 包进 `<textarea>`（`json.dumps` 会破坏原文转义字符）；llm-subtrans `#N / Original> / Translation>` 块。弱模型用容错编号行+正则回退；强模型(DeepSeek json_object)可 schema JSON。
- **稳定 ID + 破连号偏置**：用全局稳定 ID；stri8ed 甚至把 ID 随机化(连续数字让模型易跳过/合并相邻段)。
- **批大小动态**：GalTransl 失败 ÷2、连成 3 次 +1、per-model 记忆安全尺寸。
- **预过滤不翻项**：纯数字/空白/标点/扩展名/代码前缀标 EXCLUDED。
- **术语 Sentinel 物理替换**(LunaTranslator)：把 glossary 源替成 `ZX{i}Z` 调用后还原——即使模型无视指令也保术语(最强保真)。
- **按源结构重对齐输出**(AiNiee 关键)：取最后一个 `<textarea>`，**按每个源段换行数重分配**输出行，不信任模型给的 key。
- **多模式正则解析 + 6 级回退 + 原译互换修复**(llm-subtrans)：解析出 original==translation 视为填反并换回。
- **JSON 形状修复**(BallonsTranslator 金标准)：剥围栏 → Pydantic 校验 → 裸 dict/list 强转回 canonical → 按 `range(1,n+1)` 重排。
- **校验关卡**(AiNiee 有序、首错即返)：含拒答符 → 行数/键连续 → 非空 → 前缀对 → 换行数对等 → 输出≠源 → 残留源语言 → 占位符存活。
- **重复退化检测**：**zlib 压缩比** `len/len(zlib.compress)` 高=循环退化(stri8ed)——与本项目 Qwen3-ASR 重复环同源，**MT 输出同样适用**。
- **只重试失败段**：缓存为真相源只发 cache-miss，失败加 `(Failed)` 前缀下轮再发(可续传)；行数不符→批减半到 1 再爬升。
- 来源：AiNiee https://github.com/NEKOparapa/AiNiee · llm-subtrans https://github.com/machinewrapped/llm-subtrans · GalTransl https://github.com/GalTransl/GalTransl

### 1.3 Office / Markdown / HTML / EPUB 的结构保留
- **Run 合并(必做)**：Word 把一句拆成多 run(拼写/修订/字体微差)，逐 run 翻译质量崩。按相同样式深度比较折叠相邻 run（wheeled `same_style_runs` / wordflux `RunInfo` 等值键），或包成 `<R0>…</R0>` 整句送 LLM 让模型自分配。
- **写回**：①首 run 承载译文、其余 XML 级删除(事实标准，代价是句内格式归并)；②`para.clear()` 后按 RunInfo 逐 run 重建(适合 LLM 流水线+checkpoint，更保格式)。
- **容器别漏**：docx 页眉页脚在 `section.header/footer` 不在 `document.paragraphs`；图表/SmartArt 当 zip 改 `<a:t>`；pptx 查 `has_text_frame`/`has_table`/`shape_type==6`(GROUP 递归)/`notes_slide`；comments/footnotes 用 docx2python 只读抽。
- **XLSX**：`load_workbook(data_only=False)`(否则丢公式)；只翻 `cell.data_type=='s'`(跳数值'n'/公式'f'/布尔'b')；样式/sharedStrings 全交 openpyxl，切勿手改 XML。
- **HTML**：BeautifulSoup→ITag 树，`depth==2` 整句翻一次+内联子串各翻一次，`find(inner)` 定位回插(-1/重叠则 bail)；`code/script/style` + `translate="no"` 跳过。
- **Markdown 占位符掩码两条血泪教训(直接关系本项目)**：①**别用懒惰/嵌套量词正则匹配代码**——fenced 逐行 CommonMark 围栏扫描、inline code 手写 N-backtick 匹配器(`(?:[^`]+|...)+?` 会指数回溯，28 字符卡 1.8s)；②**抗碰撞+定点恢复**——计数器跳过源中已有占位符、恢复迭代到不动点(≤10 轮)、函数式 replace 防 `$$`/`$&` 坑。**粗斜体故意不掩码**(会切断句子)。
- **EPUB**：ebooklib `get_items_of_type(ITEM_DOCUMENT)`(免手解 OPF) + `html.parser`(耐脏) + 克隆 `insert_after` 出双语；`data-content-id=sha256(text)` 标记可续传幂等。
- 来源：wordflux https://github.com/pnnbao97/wordflux · argos translate-html https://github.com/argosopentech/translate-html · md-translator https://github.com/rockbenben/md-translator · bilingual_book_maker https://github.com/yihong0618/bilingual_book_maker

### 1.4 占位符掩码的通用警示（重要）
- **哪些 token 稳健**：`{0}`/`{name}` 能存活；**`%1$s` 易坏**(BPE 拆成 `%/1/$/s` 多子词各自可被丢/重排) → 多参数优先命名占位符。
- **不要过度掩码**：实体被掩成不透明符号后模型失去性/数/语序判断 → **降低 MT 质量**。只掩必须不变的(格式符/URL/代码)，**术语用 glossary 而非掩码**。
- **校验分两级**：Error(占位符不符/标签失衡/forbidden 违规/空译 → 阻断重试) vs Warning(长度比异常/句末标点/与源相同/术语未命中 → 转人工)——Pontoon 模型，正是本项目 needs_review 三终态的延伸。
- **RTL**：阿/希语里 LTR token(`%s`/URL/数字)方向继承错乱 → 每个插值用 **FSI…PDI**(U+2068…U+2069)包裹(Fluent `useIsolating`)。
- 来源：DeepL XML handling · Weblate checks · translate-toolkit `checks.py` · Fluent Unicode Isolation。

### 1.5 与 LinguaHaru 现状对照 + 建议
现状：`placeholder_mask.py`(PUA sentinel 掩 `%s`/`${var}`/`{ICU}`)、`text_separator.py`(token 预算分块 + CJK 句界 `[。！？]` + value 去重 + glossary 按段过滤)、`translation_checker.py`(JSON 解析 + `{{}}`/`[formula_n]` 占位符校验 + 目标语言字符检测 + 失败重试 + needs_review 三终态)、`coverage.py`、致命 4xx fast-exit、BabelDOC PDF、OCR 自动语言。
- **P0**：① **占位符 round-trip multiset 校验**(现有 unmask 不校验 sentinel 是否原样返回 → 四种损坏模式作清单)；② **长度比 + zlib 重复退化 QA 告警**(归 coverage/needs_review，告警转人工不硬阻断)；③ **术语合规检查**(源含 src_term 验证译文含 dst_term)。
- **P1**：④ **只重试失败段 set-diff + 批减半**(扩展现有 failed.json)；⑤ **结构重对齐 + 原译互换修复**(从 key 漂移恢复而非直接判失败)；⑥ Markdown 若支持→代码掩码两教训；⑦ RTL→FSI/PDI 隔离。
- **P2**：⑧ docx/pptx/xlsx 写回(run 合并 + 首run承载/RunInfo 重建 + 三容器查全)；⑨ EPUB/HTML 克隆 insert_after 双语 + 条数校验。
- 来源同上。

---

## §2 字幕 / 视频 STT（含 JAV / 表演型日语）

### 2.1 PRE（STT 之前）
- **16k/mono 抽取**（唯一硬性）：`ffmpeg -ac 1 -ar 16000`；whisperX/whisper.cpp 硬编码 16000。
- **响度归一**：是"卫生步骤"非提准；要做用 two-pass `loudnorm=I=-16:TP=-1.5:LRA=11` 或简单增益到固定 dBFS（VideoLingo −20 dBFS）。
- **通用降噪默认有害**：RNNoise/arnndn/noisereduce 改变 Whisper 依赖的频谱表征反降准（openai/whisper Disc #2125；"When De-noising Hurts" arXiv 2512.17562）。要做用 ffmpeg `arnndn` 或 stable-ts 可插拔 denoiser 做 A/B。
- **人声分离条件式**：BGM/音乐重才开（`htdemucs --two-stems=vocals` 或 UVR Kim Vocal 2/BS-RoFormer），普通语音/呻吟反而诱发幻觉，且**务必叠 VAD**。最大收益是"用分离音频做切分边界"而非一定喂给 Whisper（ALT 论文 arXiv 2506.15514）。
- **强制源语言**：`language="ja"` 既防误判又更快（auto-detect 只看前 30s）。
- **VAD 选 Silero（召回高）**；表演型轻声/喘息把 `threshold` 0.5→**0.3**、`speech_pad_ms` 调大、`min_speech_duration_ms` 调小。**低阈值必须配合后处理反幻觉**。
- **段长**：`max_speech_duration_s` 在最静点切（防硬切）；30s 上限对齐 Whisper 感受野；相邻 gap≤0.5s 合并。

### 2.2 POST（STT 之后）
- **反幻觉**：`condition_on_previous_text=False`（防 loop 第一手）；依赖 VAD 时把 `no_speech_threshold/log_prob_threshold/compression_ratio_threshold` 设 None 避免误删中/日文段（faster-whisper Disc #349）；`hallucination_silence_threshold`（需 word_timestamps）。
- **重复折叠**：正则检测 ≥3 词紧邻重复迭代折叠；gzip 压缩比检测重复；`no_repeat_ngram_size`/`repetition_penalty` 有性能代价（仅出 loop 时用）。
- **已知幻觉短语 blocklist**：日语「ご視聴ありがとうございました」、英语 "Thanks for watching"、`♪♪`；现成数据集 `sachaarbonel/whisper-hallucinations` 可 drop-in。
- **ITN + 标点**：SenseVoice `use_itn=True` 一遍出；无标点用 `deepmultilingualpunctuation`。
- **词级时间戳 + 重切 cue**：whisperX 强制对齐(wav2vec2) / stable-ts `regroup`（split_by_punctuation/gap/length + clamp_max）；目标 ≤42 字/行、~15–17 CPS、1–7s。
- **日语跨 cue 句切**：`ja_sentence_segmenter`（规则、按粒子重接）+ fugashi/MeCab 分词。
- **SDH 去留做开关**：默认剥离 `(moans)` 等；需要时括号保留。

### 2.3 JAV/表演型日语最有效（优先级）
- **P0 换模型 anime-whisper**（CER 13% vs large-v3 16.5%，忠实转写呻吟/喘/笑、低幻觉、NSFW 可用）；**严禁传 initial_prompt**；`no_repeat_ngram_size=0` 起步只在出 loop 时才加。
- **P0 VAD 阈值 0.3** 抓轻声/喘息（配合反幻觉）。
- **P1 解码期+事后反幻觉**；可整体参考 **WhisperJAV**（为 JAV "acoustic hell" 而生：Silero/Auditok/TEN 选 VAD、Aggressive/Conservative 模式、BS-RoFormer 分离、defensive decoding、正则剥离 `(moans)`）。
- **P2 条件式人声分离**（仅 BGM 重）；cue 后处理 stable-ts regroup。
- **反模式**：通用降噪默认开 / 人声分离常开 / 音乐邻近用 large-v3（多证据 v3<v2）/ 给 anime-whisper 传 initial_prompt。

### 2.4 与 LinguaHaru 现状对照
已做：anime-whisper 已接入、VAD 0.35/160 默认（可调到 0.3）、强制语言、whisper hallucination_silence、重复折叠、SDH 开关、音轨选择、CPS/行宽 QA、per-model 参数。
**可补**：(a) 日语幻觉短语 blocklist 后过滤（HF 数据集 drop-in）；(b) gzip 压缩比重复检测；(c) 依赖 VAD 时三阈值设 None 避免误删中/日文；(d) WhisperJAV 式按内容选 VAD + 敏感度模式。
来源：anime-whisper https://huggingface.co/litagin/anime-whisper · WhisperJAV https://github.com/meizhong986/WhisperJAV · whisperX · faster-whisper · stable-ts · ja_sentence_segmenter。

---

## §3 实时语音翻译

### 3.1 三大 commit 流派
1. **稳定前缀 LocalAgreement-2**（whisper_streaming/WhisperLiveKit）：重复解码增长缓冲，只 commit 连续两次一致的最长公共前缀，尾巴持续重写。**社区主流，也是 LinguaHaru 现 stable-prefix 所属流派。**
2. **学习型单调策略**（SeamlessStreaming EMMA / StreamSpeech）：需端到端训练，**不适合级联架构**。
3. **整句最终化**（RTranslator）：检测说话结束再整段翻译，零闪烁但延迟最高。
- **本项目（现成 STT+LLM 级联）→ 流派 1+3 混合**：LocalAgreement 出临时字幕，标点/静音处整句最终化再送 LLM。

### 3.2 PRE
- **分段用 VAD 端点而非固定窗**；两级 VAD（WebRTC 预筛 → Silero 确认+端点）。
- **Silero 双阈值滞回**：start prob≥0.5、end<0.35；流式 `min_silence_duration_ms≈500`（别用默认 2000）；`speech_pad_ms≈300–400`。
- **端点 = 持续静音** `post_speech_silence_duration≈0.5–0.7s`、`min_length_of_recording≈0.5s`。
- **pre-roll 环形缓冲（必做）** ~1.0s 防丢首音素。→ LinguaHaru `vad-worklet.js` 已有。
- **partial/final**：双模型（tiny 临时 + 准模型最终）或单模型+重复计数 finalize。

### 3.3 POST
- **LocalAgreement-2**：当前 new 与上次结果求最长公共前缀，相同 commit、不同 break；1–5 词 n-gram 去重吸收时间戳误差；**已确认 append-only，未确认尾段每次重写**。
- **子句切分送翻译**：句末标点、逗号(非列表)、分号切子句送 MT；语法感知切分(SASST)更好（≤7 token/块、语义完整）。
- **重译稳定化**：mask-k（临时译文隐藏尾部 k≈2-3 个 token，整句完成才全显）+ biased beam（偏向上版译文，erasure 降 20×）。
- **跨句上下文**：喂最近 ~150–200 词已确认文本（whisper_streaming 用 200 词）；句完成检测用标点而非纯停顿。
- **流式重标点**（小滑窗 K≈2 lookahead）；**ITN** 用 WeTextProcessing(`wetext`，zh/en/ja，有 C++ 流式 runtime）。
- **流式 LID**：累积 ~2–3s 再判、prob<0.6 兜底、后续 chunk 重跑（别锁死首判）。

### 3.4 与 LinguaHaru 现状 + 建议
现状：客户端能量 VAD + pre-roll → SenseVoice/Qwen → translate_text_simple；已有 stable-prefix 标点切句。
- **P0**：能量 VAD 后加一级 Silero 确认 + 显式端点参数；commit 从"停顿"改为"标点/子句边界"；翻译喂最近 ~150–200 词上下文。
- **P1**：临时字幕 LocalAgreement-2（无词级时间戳就 token/char 级 LCP）+ confirmed/provisional 两层显示；mask-k + 译文偏置降闪烁；LID 延迟判定。
- **P2**：流式重标点；`wetext` ITN；术语 glossary 注入。
- **不建议**：EMMA/StreamSpeech（需训练，仅作延迟预算参考，目标端到端<2s）。
来源：whisper_streaming https://github.com/ufal/whisper_streaming (arXiv 2307.14743) · RealtimeSTT · WhisperLive · SASST arXiv 2508.07781 · mask-k arXiv 1912.03393。

---

## §4 图片 / OCR / 漫画翻译

### 4.1 五阶段共识
检测(气泡/文字) → OCR → inpaint(抹原文+补背景) → 翻译 → 回贴渲染。核心数据结构 `TextBlock`（原文/译文/坐标/字体/描边）。

### 4.2 PRE
- **漫画专用检测器**（不要用通用 CRAFT）：comic-text-detector（YOLOv5+U-Net，一次出 bbox+text-line+像素 mask）或 RT-DETR；分 **bubble vs free-text** 两类（决定 inpaint 策略）。
- **按源语言选 OCR**：日→**manga-ocr**（整块多行单次、支竖排振假名，省切行）、韩→Pororo、中/英/拉丁/西里尔→PaddleOCR 对应模型。→ **LinguaHaru 已有「按源语言自动映射 OCR」，与 comic-translate 核心做法一致。**
- **像素 mask** 比框更紧（OCR 噪声少 + inpaint 只抹笔画）。
- **阅读序**：面板右→左/上→下，组内气泡右上→左下；`rtl=true`。
- **小字上采样**（esrgan/waifu2x）后 OCR，处理完缩回。
- **以气泡/块为翻译单元**，整页带上下文一起给 LLM。
- 彩漫**不要盲目二值化**（毁叠画面彩字）；二值化主要用于扫描文档/黑白漫。

### 4.3 POST
- **Inpaint**：**LaMa（manga-finetune）首选**（manga-image-translator `lama_large`、comic-translate）；轻量 AOT-GAN；纯色背景 OpenCV Telea。**mask 外扩 ~30px**（`mask_dilation_offset`）抹净笔画+描边。
- **重排版（最难）**：字号自适应不靠硬缩放，优先调行高/字距/断行；**CJK 逐字换行**（无连字符）、Latin 控 hyphenation；设 `font_size_minimum` 兜底。
- **竖排 CJK**（koharu 最具体）：块高>宽→VerticalRl；HarfBuzz + OpenType `vert/vrt2` 竖排字形；**全角标点按 ink-bounds 重居中**。
- **描边/字色从原文采样继承**（BallonsTranslator）；`font_color="fg:stroke"`。
- **SFX vs 对话**：气泡内正常译；画面 SFX 艺术字常**单独处理或跳过**（`ignore_bubble` 面积阈值）。
- **日文 kinsoku 禁则断行**：开源普遍缺失，可作差异化亮点。

### 4.4 扫描文档分支（与漫画不同）
deskew（性价比最高，1–2° 就掉准）→ 自适应二值化(Otsu/高斯)→ 去噪(Median)→ 小字上采样 → docTR/PaddleOCR 或 OCRmyPDF；**保留版面叠文本层，不走 inpaint/重排**。

### 4.5 与 LinguaHaru 现状 + 建议
已做：OCR 自动语言映射、PaddleOCR/RapidOCR、像素级处理。
- **P0**：漫画专用检测（分 bubble/free-text）；以气泡为翻译单元带上下文；LaMa-manga inpaint + mask 外扩。
- **P1**：阅读序排序；小字上采样；竖排 CJK 渲染（HarfBuzz vert + 全角标点重居中）；字号自适应（调行高/断行非硬缩放）；描边/字色继承。
- **P2**：SFX 单独策略；OCR 置信度过滤；日文 kinsoku 禁则。
来源：manga-image-translator https://github.com/zyddnys/manga-image-translator · comic-translate · BallonsTranslator · comic-text-detector · manga-ocr · koharu · PaddleOCR/RapidOCR。

---

## 总优先级速览（跨任务）

| 优先级 | 通用(§0) | 字幕STT(§2) | 实时(§3) | 图片(§4) |
|---|---|---|---|---|
| P0 | prompt-cache 前缀序 / 匹配门控术语 / 后处理校验 | anime-whisper / VAD 0.3 / 反幻觉 | Silero 确认+端点 / 标点切句 commit / 上下文 | 漫画检测 / 气泡为单元 / LaMa inpaint |
| P1 | 智能修复链 / 错误分类 | blocklist / gzip 重复 / 三阈值 None | LocalAgreement-2 / mask-k / LID 延迟 | 阅读序 / 竖排渲染 / 字号自适应 |
| P2 | 1 遍反思精炼 | 条件式人声分离 / regroup | 重标点 / wetext ITN | SFX 策略 / kinsoku |
