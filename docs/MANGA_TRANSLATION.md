# 漫画翻译（Manga Mode）调研 + 设计 + 集成方案

> 目标：给扫描版/图片版日文漫画（多为 PDF）提供高质量翻译——气泡整句翻译、原文消除、竖排中文排版——效果对齐 manga-image-translator (MIT)。
> 本文记录：① 2026 开源项目调研与本地实测；② 技术借鉴点；③ 集成决策；④ 实现方案与验收。

## 1. 调研 + 本地实测（2026-06）

| 项目 | 最新 | 本地实测 | 技术栈 | 能否集成（无头/Deepseek/依赖） | 结论 |
|---|---|---|---|---|---|
| **zyddnys/manga-image-translator (Python)** | 2025/05 | ✅ Docker + 独立venv + **现代库(numpy2/pydantic2.13/openai2.x)** 三种都跑通；10页4min(CPU) | comic-text-detector + manga_ocr + LaMa + manga2eng；含 Deepseek 后端 | ✅ 可库/CLI/API；现代库可跑（老 pin 是保守值，非硬依赖） | **质量与可集成性最佳的参照** |
| **frederik-uni/manga-image-translator-rust** | v0.12.2 2025/10 | ⚠️ 预编译 Win 二进制下载成功，但**启动 panic**（papago 初始化 `unwrap on None`，读配置前就崩）；二进制 bug，配置改不动 | Rust + ONNX(自带 CUDA/DirectML/TensorRT DLL → 原生 GPU) | ❌ 当前二进制不可用；要自己 build Rust | 思路可借鉴（GPU、渲染器），二进制暂不可用 |
| **ogkalu2/comic-translate** | v2.6.7 活跃 | 研究（GUI-only 无法无头实测） | RT-DETR-v2(11k漫画) + manga/anime LaMa；原生 PDF/EPUB/CBR/CBZ | ❌ 仅 GUI、无 Deepseek（只 GPT/Claude/Gemini）、无 CLI | 不可嵌入自动流程 |
| **dmMaze/BallonsTranslator** | 活跃 | 研究（GUI/CAT 工具） | 全家桶模块化 | ❌ 交互式桌面工具 | 适合人工精修，不可嵌 |
| **TareHimself/manga-translator** | 2025/12 | 研究 | YOLO(**AGPL**) + DeepFillV2/LaMa；**仅横排排版** | ❌ AGPL 许可、横排（漫画要竖排，比我们还差）、无 Deepseek | 不适合 |
| mayocream/koharu | Rust | 研究 | ML Rust | 早期 | 参考 |
| Snowad14/FastMangaTranslator | — | 研究 | TensorRT + TensorRT-LLM | 极快但 TensorRT 部署复杂、绑 NVIDIA | niche，不通用 |

**实测产物**（scratchpad）：MIT docker 单页/10页 PDF、MIT 非docker、MIT 现代库、我们自研 B 原型、OCR 对比(manga_ocr vs PaddleOCR)、消字对比(LaMa vs Telea)。

## 2. 关键技术发现（决定方案的核心）

1. **OCR 不是瓶颈**：同一页上 **PaddleOCR(japan) ≈ manga_ocr**，识别结果几乎一致（仅标点差异），人名 `不死川` 两者都对。换 manga_ocr 几乎零收益。
2. **消字不是瓶颈**：白气泡场景 **cv2 Telea ≈ LaMa**；LaMa 仅在"文字压在复杂画面上"才明显更好。我们管线**已接入 LaMa**（`_lama_enabled` + `core/pipelines/lama_inpaint.py`，可选）。
3. **竖排渲染我们已有**：`image_translation_pipeline._render_vertical` 右→左列、缩字号自适应；`_reading_order` 已做竖排 RTL 阅读顺序。
4. **⭐唯一缺口 = 气泡分组**：我们管线把每个 OCR 文本行**单独翻译** → 一句话被切成碎片（`本日より`/`お世話になります`/`不死川です` 各自翻 → 人名碎成"蒙关照"）。MIT 的核心优势就是 `textline_merge`：把同气泡的多行合并成整句再翻。
5. **借鉴 MIT 的 `textline_merge`**（图连通：近邻≤~字号 + 同方向 + 字号相近 + 对齐 → 连通分量 → 组内按阅读顺序拼接）→ 我用 **~40 行纯 Python(union-find)** 重写，**实测在测试页上分出 7 组、与 MIT 完全一致**，关键合并（人名整句）全部正确。
6. **B 端到端已验证**：分组 → 整句 Deepseek 翻译 → 复用现有竖排渲染 → 输出，质量对齐 MIT（人名完整、竖排、消字干净）。

## 3. 集成决策（已定）

- **(2) 是否需要额外模型 / 是否单独插件？→ 不需要额外模型，不单独做插件。**
  B = 现有 PaddleOCR（属 **Image OCR 插件**）+ 纯 Python 分组（无模型）+ 现有竖排渲染 + 现有可选 LaMa。**零新模型 → 直接集成进现有「图像翻译」**，不新建"漫画翻译插件"。
- **(3) 模式命名 → 统一叫「漫画模式」**（PDF 与图像共用一个开关）。
  - 图像/PDF 翻译页加「漫画模式」开关。
  - **门控**：漫画模式依赖 OCR（识别气泡文字），所以需要 **Image OCR 插件**；未装 → 复用现有"请先安装 Image OCR 插件"提示。**不新增插件、不新增额外提示**（因为 B 不需要额外插件）。
- **(4a) PDF 进 → PDF 出**：漫画模式下 PDF 走"逐页渲染成图 → 分组+翻译+竖排 → 重新打包成 PDF"。
- **(4b) 校正（proofread）= 硬性要求**：漫画模式产出与现有校对兼容的 `dst_translated.json` + `manifest.json` + 原始页图，支持在「校对」页编辑译文并**重新导出 PDF**（用编辑后的文本重渲染，不重新调用 LLM）。

## 4. 实现方案

**核心新增**：`image_translation_pipeline._group_text_regions(regions, src_lang)`（OCR 后、翻译前），漫画模式开启时合并气泡。配置键 `manga_mode`（默认关）。

- **图像（.png/.jpg…）漫画模式**：OCR → 分组 → 每组整句翻译 → 竖排渲染回合并框 → 输出图。
- **PDF 漫画模式**：新增 `core/translators/manga_pdf_translator.py`（或在 pdf 路由分流）：fitz 逐页 150dpi 渲染 → 走图像漫画管线 → 收集每页译图 → fitz 重打包成 PDF（输出名 `*_src2dst.pdf`）。
- **校对**：每页产出 proofread 表（原文/译文/合并框）+ manifest（标记 manga + 页图路径）；`backend` 增加 manga PDF 的 `reexport`：读编辑后的译文 → 重渲染各页 → 重打包 PDF。
- **两端 UI（Web+Qt 对齐）**：选中 PDF/图片时显示「漫画模式」开关；逻辑/后端共用 `core/`。

**验收清单（已完成 2026-06-27，提交 3c6428a / 79ff0c2 / c6d264e）**：
- [x] 图像漫画模式：气泡整句、竖排、消字、无溢出（实测测试页 15行→6气泡，人名"不死川"完整）。
- [x] PDF 漫画模式：PDF 进 → PDF 出，全 10 页 OK（`core/translators/manga_pdf_translator.py`；页内 JPEG q88，10页~10MB）。
- [x] 校对：列出漫画 PDF、可编辑译文、重新导出 PDF（`_export_manga_pdf_proofread` + 共享 `render_manga_pages_to_pdf`；实测改字→重导出 10.4MB）。
- [x] 未装 Image OCR 插件 → 正确提示（两端 gating：manga 开 + pdf/图 → 需 Image OCR 插件）。
- [x] Web/Qt 两端一致（`#manga-options` / `_build_manga_card`，config `manga_mode` 共用）。
- [x] 全量测试 142 绿。

**实现要点回顾**：
- 图像管线拆出可复用核心 `ocr_and_group_image()`（OCR+分组，不落盘）+ `render_on_image()`（消字+渲染，返回 PIL）。
- `_group_text_regions()`：union-find 近邻+同方向+字号相近+对齐合并；组内竖排右→左拼接。
- `_render_vertical()` 重写：网格按高×宽双向自适应+居中，修复"字超出气泡"。
- PDF：`MangaPdfTranslator` 逐页栅格化→合并 src.json（全局 count_src，复用 base 批处理/历史/覆盖率）→逐页渲染→fitz 重打包(JPEG)。
- 依赖：`pymupdf` 加入 Image OCR 插件（manga PDF 需要；manga 模式由该既有插件门控，不新增插件）。
- 路由：`backend.get_translator_class` 在 `manga_mode` 开时把 `.pdf` 换成 MangaPdfTranslator；图像本就走图像管线（内部读 manga_mode）。

## 5. 被否决的方案 + 原因（备查）

- **整包 bundle MIT-Python**：实测能在现代库上跑，但带 ~30 依赖 + ~1GB 模型；做"满血可选增强插件"可行，但 B 已达核心质量，非必要。**留作未来"漫画增强"可选插件**（若要复杂网漫/极致质量）。
- **MIT-Rust 二进制**：启动 panic，暂不可用。
- **comic-translate/BallonsTranslator**：GUI-only、不可无头、(comic-translate)不支持 Deepseek。
- **TareHimself**：AGPL + 横排排版（比我们差）。
