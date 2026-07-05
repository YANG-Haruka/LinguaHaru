<div align="center">
  <img src="assets/img/ico.png" alt="LinguaHaru" height="180" />

  <h1>LinguaHaru</h1>

  <p>
    <a href="README.md">English</a> | 简体中文 | <a href="README_JP.md">日本語</a>
    <br/>
    <a href="https://github.com/YANG-Haruka/LinguaHaru/wiki/zh-Home" target="_blank">使用说明（Wiki）</a>
  </p>

  <p>
    <img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru?style=flat-square" alt="Release"/>
    <img src="https://img.shields.io/github/downloads/YANG-Haruka/LinguaHaru/total?style=flat-square" alt="Downloads"/>
    <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru?style=flat-square" alt="License"/>
    <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru?style=flat-square" alt="Stars"/>
    <img src="https://img.shields.io/badge/python-3.12-blue?style=flat-square" alt="Python"/>
  </p>

  <p><b>一键式高质量 AI 翻译 —— 文档、字幕、图片、视频与实时语音。</b><br/>
  两套精心打磨的界面（原生桌面端 + Web 控制台）共用一个久经考验的引擎，解压即用，无需安装。</p>
</div>

<div align="center">
  <img src="assets/img/sample.gif" width="92%" alt="Demo"/>
</div>

---

## 支持的格式

| 类别 | 格式 |
|---|---|
| 办公文档 | DOCX · PPTX · XLSX · PDF |
| 文本与电子书 | TXT · Markdown · EPUB · HTML · ODT · JSON · CSV / TSV |
| 字幕 | SRT · VTT · ASS · SSA · LRC |
| 图片（插件） | PNG · JPG · WebP · BMP …… 含扫描版漫画 |
| 视频与音频（插件） | MP4 · MKV · MP3 · WAV …… 自动转写并翻译字幕 |
| 实时语音（插件） | 麦克风或系统声音，边说边译 |

## 为什么选 LinguaHaru

**为质量与速度而生的翻译引擎。** 智能分批、并发请求与按模型限流、翻译缓存、术语表强制、占位符保护、译后 QA 检查。原子化增量写盘让任何任务都崩溃安全；中断的任务可以精确续传，6.0 引擎比上一代提速 90% 以上。

**两套完整前端，一个后端。**
- **桌面端** —— Fluent Design 原生 Windows 应用（浅色/深色、悬浮实时字幕、全局拖拽）。
- **Web 控制台** —— 浏览器里的同款全功能，支持多用户会话隔离、局域网共享与公共服务器模式。

**重的东西全是插件。** 基础包保持小巧，需要什么在应用内插件页装什么。自动检测 NVIDIA 显卡并安装 GPU 运行时（CUDA torch、onnxruntime-gpu）；卸载插件会完整回收它带来的所有依赖。

| 插件 | 提供的能力 | GPU |
|---|---|---|
| PDF | 保留排版的 PDF 翻译（BabelDOC 引擎） | — |
| 图片 OCR | 图片翻译并回填文字；PP-OCRv6 | 自动（提速约 60×） |
| 漫画模式 | 扫描漫画/PDF 的气泡识别、抹字、译文回填，支持校对后再导出 | 自动 |
| 视频/音频 | 语音转写（SenseVoice、Whisper、Qwen3-ASR）+ 字幕翻译；内置 ffmpeg | 自动 |
| 实时语音 | 流式字幕、整句修正、防幻觉、术语加固 | 自动 |
| 语音输入 | 速译页的麦克风输入与朗读 | 自动 |

**日常好用的细节。** Google 风格的速译（带语音输入与朗读）；完整词汇表管理（新建/导入/删除），文档、速译、实时翻译全局套用；项目级历史与一键续译；实时 token 与费用估算；应用内校对编辑器并可重新导出；首次运行交互式引导；13 种完整本地化界面语言。

**安全稳定。** 致命 API 错误（密钥无效、余额不足）自动识别并用你的语言解释；失败/中断任务进历史、可续译；Web 端按用户会话隔离 + CSRF 防护；SHA-256 校验的自更新，失败自动回滚。

**对中国大陆网络友好。** 依赖、模型、更新全链路自动回退到高速镜像（清华 PyPI、hf-mirror、ModelScope、百度 BOS、ghproxy），无需科学上网。

## 快速开始

### 方式 A（推荐）：便携版

不需要 Python，不需要配 CUDA。下载、解压、双击。

1. 从 [Releases](https://github.com/YANG-Haruka/LinguaHaru/releases/latest) 下载：
   - `LinguaHaru-*-desktop-portable.zip` —— 原生桌面端
   - `LinguaHaru-*-web-portable.zip` —— Web 端（自动打开浏览器）
2. 解压到任意位置，运行 `Start-Desktop.bat` 或 `Start-Web.bat`。
3. 在 **接口管理** 添加接口（如 DeepSeek），粘贴 API 密钥，点击卡片激活。
4. 需要 PDF / OCR / 视频 / 实时语音？在 **插件** 页一键安装 —— GPU 自动处理。
5. 有新版本时应用内一键自更新 —— 已装插件、模型与设置全部保留。

### 方式 B：源码运行

```bash
conda create -n lingua-haru python=3.12 && conda activate lingua-haru
pip install -r requirements/base.txt

pip install -r requirements/web.txt   # Web:   python -m webapp.server
pip install -r requirements/qt.txt    # 桌面:  python app_qt.py
```

可选插件可在应用内插件页安装，或手动：

```bash
pip install -r plugins/pdf/requirements.txt       # PDF（BabelDOC）
pip install -r plugins/ocr/requirements.txt       # 图片 OCR + 漫画模式
pip install -r plugins/video/requirements.txt     # 视频/音频 + 实时语音
pip install -r plugins/speechio/requirements.txt  # 语音输入 + 朗读
```

### 推荐引擎

**[DeepSeek](https://platform.deepseek.com/) 最新 Flash 模型** —— 快、好、便宜。在接口管理里粘贴 API 密钥并激活即可。

本地模型（Ollama / LM Studio）适合离线或隐私敏感场景，但多数情况下在线 API 明显更快更好。

## 界面一览

<table>
  <tr>
    <td><img src="assets/img/screenshots/translate-done.png" alt="一键文件翻译 + 实时仪表盘"/></td>
    <td><img src="assets/img/screenshots/plugins.png" alt="按需插件 + GPU 自动启用"/></td>
  </tr>
  <tr>
    <td align="center"><sub>一键文件翻译 + 实时仪表盘</sub></td>
    <td align="center"><sub>按需插件 + GPU 自动启用</sub></td>
  </tr>
  <tr>
    <td><img src="assets/img/screenshots/quick-translate.png" alt="速译（带语音输入）"/></td>
    <td><img src="assets/img/screenshots/live-voice.png" alt="实时语音字幕"/></td>
  </tr>
  <tr>
    <td align="center"><sub>速译（带语音输入）</sub></td>
    <td align="center"><sub>实时语音字幕</sub></td>
  </tr>
  <tr>
    <td><img src="assets/img/screenshots/translate-light.png" alt="浅色主题"/></td>
    <td><img src="assets/img/screenshots/history.png" alt="历史记录 + 一键续译"/></td>
  </tr>
  <tr>
    <td align="center"><sub>浅色主题</sub></td>
    <td align="center"><sub>历史记录 + 一键续译</sub></td>
  </tr>
</table>

## 部署方式

| 模式 | 方法 |
|---|---|
| 桌面 | `Start-Desktop.bat`（便携版）或 `python app_qt.py` |
| 本机 Web | `Start-Web.bat`（便携版）或 `python -m webapp.server` —— 从 8080 起自动选空闲端口 |
| 局域网共享 | Web 设置里打开局域网模式，同一网络所有设备共用一台主机 |
| 公共服务器 | 服务器模式对访客隐藏密钥/模型管理，使用主机的密钥提供服务 |
| Docker | `docker compose up -d` —— 见 `Dockerfile` / `docker-compose.yml`；支持 `HOST` / `PORT` / `ADMIN_PASSWORD` |

## 项目结构

```
core/                后端 —— 全部非 UI 逻辑
  engine/            翻译引擎（分批、重试、QA、缓存）
  translators/       各格式翻译器（docx、pptx、xlsx、pdf、srt……）
  pipelines/         抽取/回填管线 + 媒体（STT）/ 图片（OCR）/ 漫画
  llm/               LLM API 封装（在线 / 本地）
webapp/              Web 前端 —— FastAPI + 静态 HTML/CSS/JS
qt_app/              桌面前端 —— PySide6 + Fluent Widgets
plugins/             可选插件清单（应用内按需安装）
config/              静态配置 —— 提示词、语言包、接口配置、默认设置
assets/              静态资源 —— 图标、图片、tiktoken 数据
glossary/            种子词汇表（Default.csv）
requirements/        base.txt + 各前端附加依赖
tools/               构建脚本（便携包构建器）
tests/               测试套件（格式语料、Web 会话、i18n、更新器……）
```

## 联系方式

<div align="center">
  <a href="https://www.harukayang.com/" target="_blank">个人主页</a> ·
  <a href="https://www.linkedin.com/in/yang-haruka/" target="_blank">领英</a> ·
  QQ 3234306205 · 微信 HarukaQnQ
</div>

## 软件声明

本软件基于 GPL-3.0 协议完全开源，可自由使用。
软件仅提供基于 AI 的翻译服务，作者不对翻译内容承担任何责任。
请确保您的使用符合相关法律法规。
署名永远令人开心~ 但完全是自愿的 (´ω｀)♡
