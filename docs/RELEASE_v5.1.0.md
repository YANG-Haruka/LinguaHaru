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
LinguaHaru-web.zip      8bcedf2242027ea6fc30805cd821895d2c83ab798760005a3ac3ff67762700d3
LinguaHaru-desktop.zip  d1d7c69aff76341b4cad151be4a05572b45c566b163f758ec575c5e085f47711
```
> 这两个哈希已写入 `version.json`(assets[flavor].sha256),智能更新已启用。
> ⚠️ 上传到 GitHub Release 的 zip 必须是本次构建的同一文件,否则哈希不匹配、自更新会拒绝。
> The zips uploaded to the GitHub Release must be these exact files, or the hash
> won't match and self-update will (correctly) refuse.
