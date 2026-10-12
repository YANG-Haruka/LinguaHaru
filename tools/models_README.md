# LinguaHaru 模型包 / Model packs

每个模型单独一个压缩包，**只下载你要用的功能对应的包**。下好以后解压进程序的 `models` 文件夹，程序会直接识别为「已安装」，不用再联网下载。

不下载这些包也可以：在程序的「插件」页点安装，模型会自动在线下载。这些包是给网络慢、连不上 Hugging Face，或想一次装好、离线使用的人准备的。

## 怎么装

1. 找到程序文件夹，也就是解压出来的 `LinguaHaru-desktop` / `LinguaHaru-web` / `LinguaHaru-desktop-mac`。里面和 `Start-Desktop.bat` / `LinguaHaru.app` 同一层有一个 `models` 文件夹，没有就新建一个。
2. 把下载的 zip **解压到 `models` 文件夹里**。压缩包里已经带好了正确的子目录，直接解压、遇到同名文件选覆盖即可。
3. 打开程序，在「插件」页能看到该模型显示为已下载，选中它就能用。

> 解压后的结构类似 `models/hub/…`、`models/whisper/…`、`models/rapidocr/…`。不要再多套一层文件夹（例如 `models/stt-whisper-small/hub/…` 是错的）。

## Windows 和 Mac 怎么区分

- 文件名以 **`-mac`** 结尾的，**只给 Mac**（Apple M 系列芯片）。
- `stt-qwen3-asr-0.6b.zip` 和 `stt-qwen3-asr-1.7b.zip`（不带 `-mac`）**只给 Windows**。
- 其余所有包 Windows 和 Mac 都能用。

**为什么 Qwen3-ASR 有两套？** Windows 版是原始的 PyTorch 格式，跑在 NVIDIA 显卡上；Mac 版是苹果 MLX 框架的 8-bit 格式，跑在 M 系列芯片自带的 GPU 上，体积只有一半左右。两种格式不能互换。

## 我该下哪个？

| 我想用的功能 | 推荐下载 |
|---|---|
| 实时语音翻译（Windows） | `stt-sensevoice-small.zip`（默认） |
| 实时语音翻译（Mac） | `stt-qwen3-asr-1.7b-mac.zip`（Mac 默认，最准） |
| 视频 / 音频生成字幕 | `stt-sensevoice-small.zip`（默认）；想更准可加 `stt-whisper-large-v3-turbo.zip` 或 Qwen3-ASR |
| 日语动画、游戏等情绪化的配音 | `stt-anime-whisper.zip` |
| 图片翻译 | `ocr-small.zip`（默认） |
| 漫画翻译 | `ocr-medium.zip`（漫画默认，最准） |
| PDF 翻译 | `pdf.zip`（必需） |
| 图片翻译时更干净地抹掉原文 | `image-inpaint-lama.zip`（可选，在「设置 → 翻译选项 → 高级选项」里打开「图片高质量抹字 (LaMa)」） |

## 全部模型

| 文件 | 适用系统 | 大小 | 用途 |
|---|---|---|---|
| `stt-sensevoice-small.zip` | Windows / Mac | 947 MB | 语音识别 SenseVoice（含断句模型）。中 / 英 / 日 / 韩 / 粤语，速度快。Windows 实时语音和字幕的默认模型 |
| `stt-whisper-tiny.zip` | Windows / Mac | 76 MB | 语音识别 Whisper tiny：最小最快，准确率低 |
| `stt-whisper-base.zip` | Windows / Mac | 146 MB | 语音识别 Whisper base：轻量 |
| `stt-whisper-small.zip` | Windows / Mac | 486 MB | 语音识别 Whisper small：速度和准确率均衡 |
| `stt-whisper-large-v3-turbo.zip` | Windows / Mac | 1.6 GB | 语音识别 Whisper large-v3-turbo：多语言，准确且快（推荐） |
| `stt-whisper-large-v2.zip` | Windows / Mac | 3.0 GB | 语音识别 Whisper large-v2：高精度，体积大 |
| `stt-anime-whisper.zip` | Windows / Mac | 3.0 GB | 语音识别 Anime-Whisper：只支持日语，擅长动画 / 游戏里情绪化的语音 |
| `stt-qwen3-asr-0.6b.zip` | **Windows** | 1.9 GB | 语音识别 Qwen3-ASR 0.6B：支持 30 多种语言，准确 |
| `stt-qwen3-asr-1.7b.zip` | **Windows** | 4.6 GB | 语音识别 Qwen3-ASR 1.7B：最准，建议有 6 GB 以上显存的 NVIDIA 显卡 |
| `stt-qwen3-asr-0.6b-mac.zip` | **Mac** | 1.0 GB | 语音识别 Qwen3-ASR 0.6B（MLX 8-bit）：更轻更快 |
| `stt-qwen3-asr-1.7b-mac.zip` | **Mac** | 2.4 GB | 语音识别 Qwen3-ASR 1.7B（MLX 8-bit）：Mac 实时语音的默认模型，最准 |
| `ocr-tiny.zip` | Windows / Mac | 20 MB | 图片文字识别 PP-OCRv6 tiny：最快 |
| `ocr-small.zip` | Windows / Mac | 65 MB | 图片文字识别 PP-OCRv6 small：图片翻译默认 |
| `ocr-medium.zip` | Windows / Mac | 239 MB | 图片文字识别 PP-OCRv6 medium：最准，漫画翻译默认 |
| `pdf.zip` | Windows / Mac | 256 MB | PDF 翻译必需：版面识别模型 + 字体 |
| `image-inpaint-lama.zip` | Windows / Mac | 206 MB | LaMa 抹字：图片翻译时更干净地抹掉原文（可选） |

## 校验

`SHA256SUMS.txt` 里有每个压缩包的 SHA-256。下载很大的文件后，如果解压报错，可以对照校验值确认文件是否完整。

---

## English

One zip per model — **download only the ones for the features you use**, unzip them **into the app's `models` folder** (inside the extracted `LinguaHaru-desktop` / `LinguaHaru-web` / `LinguaHaru-desktop-mac` folder, next to `Start-Desktop.bat` / `LinguaHaru.app`; create it if missing), and the app treats the model as installed — no download needed. The zips already contain the right sub-folders (`models/hub/…`, `models/whisper/…`, `models/rapidocr/…`); don't add an extra folder level. You can also skip the packs entirely and install models from the Plugins page.

- Files ending in **`-mac`** are Mac-only (Apple Silicon). `stt-qwen3-asr-0.6b.zip` / `stt-qwen3-asr-1.7b.zip` (no `-mac`) are Windows-only. Everything else works on both.
- Qwen3-ASR comes twice because the formats aren't interchangeable: PyTorch weights for Windows, Apple MLX 8-bit weights (about half the size, running on the M-series GPU) for the Mac.
- Quick picks: real-time voice — `stt-sensevoice-small.zip` on Windows, `stt-qwen3-asr-1.7b-mac.zip` on the Mac; subtitles — SenseVoice (default) or `stt-whisper-large-v3-turbo`; Japanese anime / game voices — `stt-anime-whisper`; image translation — `ocr-small` (manga: `ocr-medium`); PDF translation — `pdf.zip` (required); cleaner text removal in images — `image-inpaint-lama` (optional, enable "HQ image text erase (LaMa)" in Settings → Advanced Options).
- `SHA256SUMS.txt` lists each zip's SHA-256 for checking large downloads.
