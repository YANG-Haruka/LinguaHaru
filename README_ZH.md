<div align="center">
  <img src="assets/img/ico.png" alt="LinguaHaru" id="title" style="height: 200px; width: auto;" />

[English](README.md) | 简体中文 | [日本語](README_JP.md)  
<br/><a href="https://github.com/YANG-Haruka/LinguaHaru/wiki/zh-Home" target="_blank">📚 使用说明 Wiki</a>


<div align=center><img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru"/></div>
<p align='center'>次世代AI翻译神器，一键高质精准翻译各类常用文件</p>
<h3 align='center'>支持的文件格式</h3>
<p align='center'><b>📄 DOCX</b> • <b>📊 XLSX</b> • <b>📑 PPTX</b> • <b>📰 PDF</b> • <b>📝 TXT</b> • <b>🎬 SRT/ASS/VTT/LRC</b> • <b>📘 MD</b> • <b>📚 EPUB</b> • <b>🗂 CSV/TSV</b> • <b>🌐 HTML</b> • <b>📃 ODT</b> • <b>🔤 JSON</b></p>

</div>
<h2 id="What's This">这是什么？</h2>
这款基于最前沿大语言模型的翻译工具，以极简操作提供卓越翻译质量，支持多种文档格式与语言。

它提供以下功能：

- **双前端**：网页版（浏览器界面）与桌面版（Qt Fluent Design），同一套后端，按喜好选择。
- **多格式兼容**：.docx / .pptx / .xlsx / .pdf / .txt / 字幕(srt/ass/vtt/lrc) / .md / .epub / csv / html / odt / json，以及图片、视频/音频。
- **全球语言互译**：覆盖中/英/日/韩/俄等 13+ 语言，界面同样多语言。
- **一键极速翻译**：拖入文件即翻；支持翻译模式（精确/通用）、术语表、双语对照、各格式专属选项。
- **首页「翻译」**：Google-翻译式快速短文本翻译，支持语音输入与朗读。
- **实时语音**：边说边译，自动断句，实时显示原文与译文。
- **插件系统 + 插件市场**：PDF、图片 OCR、视频/音频字幕、实时语音、语音输入做成可选插件，按需安装（自带 uv，安装快）；还可从远程市场下载第三方自包含插件，无需更新主程序。
- **智能更新**（便携版）：检测到新版一键自动更新，保留已装插件、模型和你的设置/接口。
- **新手引导**：首次打开的交互式聚光灯教程，带你认识每个页面。
- **灵活翻译引擎**：在线 API（DeepSeek / OpenAI 等）与本地模型（Ollama / LM Studio）自由切换。
- **国内友好**：HuggingFace / PyPI / GitHub 均自动探测官方，连不上时切国内镜像（hf-mirror / 清华 / ghproxy）。
- **局域网共享**（仅网页版）：一台主机，局域网内多设备共用。


<h2 id="install">安装和使用</h2>

### 方式一（推荐）：便携版，解压即用

无需 Python、无需 CUDA，下载解压双击即可。

1. 到 [Releases](https://github.com/YANG-Haruka/LinguaHaru/releases/latest) 下载：
    - `LinguaHaru-web.zip` —— 网页版（浏览器界面）
    - `LinguaHaru-desktop.zip` —— 桌面版（Qt）
2. 解压到任意目录（路径可含中文）。
3. 双击启动：
    - `Start-Web.bat` —— **会自动打开浏览器**，无需手动输网址
    - `Start-Desktop.bat` —— 桌面窗口
4. 「接口管理」添加翻译接口（如 DeepSeek），填入 API Key，点击卡片激活。
5. 需要 PDF / 图片 OCR / 视频字幕 / 实时语音时，到「插件」页**按需安装**（自带 uv，安装快；国内自动走镜像）。
6. 模型可在「插件」页按需下载；也可从网盘单独下载（见 [模型说明](docs/MODELS.md)）后解压进程序的 `models/` 文件夹。

> 检测到新版本时，便携版可一键**智能更新**，自动保留已装插件、模型与你的设置。

### 方式二：从源码运行（开发 / 进阶）

1. Python 3.12（建议用 [Conda](https://www.anaconda.com/download) 建虚拟环境）
    ```bash
    conda create -n lingua-haru python=3.12 && conda activate lingua-haru
    ```
2. 核心依赖 + 选一个前端
    ```bash
    pip install -r requirements/base.txt
    pip install -r requirements/web.txt   # 网页版：python -m webapp.server  (默认 http://127.0.0.1:8080)
    pip install -r requirements/qt.txt    # 桌面版：python app_qt.py
    ```
3. 可选插件（也可在 UI「插件」页一键安装）
    ```bash
    pip install -r plugins/pdf/requirements.txt       # PDF（BabelDOC，保排版）
    pip install -r plugins/ocr/requirements.txt       # 图片 OCR
    pip install -r plugins/video/requirements.txt     # 视频/音频字幕（内置 ffmpeg）+ 实时语音
    pip install -r plugins/speechio/requirements.txt  # 翻译页语音输入 + 朗读
    # 模型首次使用时自动下载到 models/（GPU 语音转写需自行安装 CUDA 版 torch）
    ```

### 本地大语言模型（可选）
在线 API 之外，也支持本地 [Ollama](https://ollama.com/) / LM Studio。例如：`ollama pull qwen2.5`，再到「接口管理」激活本地接口。

<h2 id="preview">预览</h2>
<div align="center">
  <img src="assets/img/sample.gif" width="80%"/>
</div>


## 参考项目
- [ollama-python](https://github.com/ollama/ollama-python)
- [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)

## 待办事项
- 添加继续翻译功能。

## 更新日志
- 2026/06
**V5.1 更新**：全新**便携版**（内嵌 Python，解压即用）；**插件市场**（按需安装 + 远程下载第三方插件）；**智能更新**（一键更新且保留插件/模型/设置）；首次打开的**新手引导**；网页版**自动打开浏览器**；模型可**单独打包从网盘下载**；国内镜像自动切换（HF/PyPI/GitHub）；大量翻译质量与稳定性修复。
- 2026/01/28
V5.0更新：更新PDF库，优化UI界面，增加更多实用化功能。感谢一年的陪伴！
- 2025/05/09
V3.0更新，增加多线程，继续翻译功能。增加Markdown文件的翻译。对Qwen3系列进行更友好的支持。优化日志显示。
- 2025/04/02  
更新到v2.3，增加自定义图标/名称，支持多任务队列。优化了翻译结果检测的逻辑。增加翻译结果与原文对比显示的功能。
- 2025/03/14
更新到V2.0，增加对Txt的支持。优化Word/Excel/长文本的翻译。增加自定义重试次数的功能。优化了翻译结果的显示。
- 2025/02/01  
更新了翻译失败文本的处理逻辑。
- 2025/01/15  
修复了PDF翻译的一个bug，添加了多语言支持，还摸了摸小猫咪。
- 2025/01/11  
添加对PDF的支持。参考项目：[PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)
- 2025/01/10    
添加了对deepseek-v3的支持。现在您可以使用API进行翻译（更稳定）。  
API获取：https://www.deepseek.com/
- 2025/01/03  
新年快乐！修订了逻辑，添加了审核功能，并增强了日志记录。


## 软件免责声明  
本软件完全开源，遵循 GPL-3.0 协议，欢迎自由使用。
软件本身仅提供 AI 翻译服务，所有翻译内容的责任与作者无关。
请用户遵守法律，进行合法、合规的翻译活动。
如果愿意署名，我们会非常感激～当然，不署名也完全没有关系哦 (´▽｀)♡
