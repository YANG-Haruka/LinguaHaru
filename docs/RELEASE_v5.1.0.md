# LinguaHaru v5.1.0

一站式 AI 翻译 — 文档、字幕、图片、实时语音。便携版,解压即用。
One-stop AI translation — documents, subtitles, images, real-time voice. Portable, unzip and run.

## 下载 / Downloads

| 版本 | 说明 | 文件 |
|---|---|---|
| **Web 版** | 浏览器界面 (FastAPI),最小 | `LinguaHaru-web.zip` (~190 MB) |
| **桌面版** | 原生 Qt 界面 | `LinguaHaru-desktop.zip` (~300 MB) |

> 模型不含在安装包内,按需在「插件」页下载,或从网盘获取 `models/` 文件夹放到程序根目录。
> Models are not bundled — download them on demand from the Plugins page, or drop a `models/` folder into the app root.

## 使用 / Usage
1. 解压到任意目录(路径可含中文)。Unzip anywhere.
2. 双击 `Start-Web.bat`(网页版,**会自动打开浏览器**,无需手动输网址)或 `Start-Desktop.bat`(桌面版)。
3. 「接口管理」添加翻译接口(如 DeepSeek),填 API Key,点卡片激活。
4. 需要 PDF / 图片 OCR / 视频字幕 / 实时语音时,到「插件」页按需安装(自带 uv,安装很快)。

## v5.1.0 亮点 / Highlights
- **便携版打包**:内嵌 Python,真实环境,插件可随时按需安装(不像冻结 exe)。Portable embeddable-Python build.
- **插件市场**:内置 5 个可选插件(PDF / 图片 OCR / 视频音频 / 实时语音 / 翻译语音输入)+ 远程市场(可下载第三方自包含插件,无需更新主程序)。Built-in + remote plugin market.
- **智能更新**(便携版):一键自动更新,保留已装插件、模型和你的设置/接口。In-app update preserving plugins/models/settings.
- **新手引导**:首次打开的交互式聚光灯教程。First-run interactive tour.
- **首页「翻译」**:Google-翻译式快速短文本,支持语音输入/朗读。
- 大量翻译质量与稳定性修复(占位符保护、长段落、字幕重切、API 错误分类、翻译缓存、PDF 润色复验等)。

## 安全 / Security
- 智能更新需 SHA-256 校验(本文件内列出各 zip 的哈希),无校验拒绝应用。
- 插件市场只从可信索引按 key 解析下载地址,zip 解压有 zip-slip / zip-bomb 防护。

## 校验和 / Checksums (SHA-256)
```
LinguaHaru-web.zip      304b953192243f005a849ce62371883d274fb93e4103770421be1087c01660ed
LinguaHaru-desktop.zip  e32b33c230ab9345896c56c360a24990240dab4865ea1ca60f85db3672a27302
```
> 这两个哈希已写入 `version.json`(assets[flavor].sha256),智能更新已启用。
> ⚠️ 上传到 GitHub Release 的 zip 必须是本次构建的同一文件,否则哈希不匹配、自更新会拒绝。
> The zips uploaded to the GitHub Release must be these exact files, or the hash
> won't match and self-update will (correctly) refuse.

## 发布顺序 / Release order (重要)
1. **先**把这两个 zip 传到 GitHub Release `v5.1.0`(`version.json` 里的 `url` 才不会 404)。
2. 上传完、确认能下载后,**再**更新远程 `version.json`(发布清单)。在资产就绪前更新清单,用户会检查到更新却下载 404。

## 中国大陆下载通道 / China download channels
- **依赖(pip)**:已内置清华 PyPI 自动兜底(官方源不可达或安装失败 → 自动切镜像重试)。`torch / paddlepaddle / qwen-asr` 等大包默认装 **CPU** 版;需要 GPU/CUDA 的用户请按自己环境手动安装对应 wheel。
- **自动更新**:更新包下载现在**多源兜底**——清单 `assets.<flavor>.urls`(可填国内 OSS/CDN,放最前)→ GitHub 直链 → GitHub 经 ghproxy 等镜像;全部用同一个 `sha256` 校验。即使只填了 GitHub `url`,大陆用户也会自动尝试 ghproxy 镜像。
- **模型**:不建议只靠首次运行在线拉取。已用 `tools/package_models.ps1` 生成各模型 zip(含 SHA-256,见 `docs/MODELS_SHA256SUMS.txt`);建议把大模型(Qwen3-ASR ~4.7G / Whisper large ~3G 等)放到国内对象存储/网盘,用户下载后解压到 `models/`。HF 走 `hf-mirror.com` 自动兜底,但公益镜像不应作为唯一生产通道。
- **插件市场**:`plugins-index.json` 目前为空。每个插件条目**必须带 `sha256`**(安装会执行下载的代码,无校验直接拒绝),可选 `size` / `urls`(镜像)。市场正式开放前请先给条目补齐校验。
- **本地安装兜底**:模型支持"解压 zip 到 `models/`";内网/无外网环境优先用本地包,比在线下载可靠。
