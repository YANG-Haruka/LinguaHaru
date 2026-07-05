# 模型单独下载 / Per-model downloads

每个模型单独打成一个 zip,放在网盘。**只下你需要的**,解压到程序的 `models/` 文件夹即可用,无需联网去 Hugging Face 下载。
Each model is packaged as its own zip. Download only what you need and unzip it
into the app's `models/` folder — ready to use, no Hugging Face download required.

## 怎么用 / How to use
1. 找到程序根目录下的 `models/` 文件夹(没有就新建一个)。
2. 把下载的 zip **解压到 `models/` 里**(zip 内已含正确的子目录结构,直接覆盖解压即可)。
3. 启动程序,在「插件」页选择对应模型即可,不会再去网上下。

> Unzip each model zip **into** `models/`. The archive already contains the correct
> sub-folders, so it lands in the right place. Then pick it in the Plugins page.

## 模型清单 / Catalog

### 语音转文字 STT(视频字幕 / 实时语音)
| zip | 说明 | 用途 |
|---|---|---|
| `stt-sensevoice-small.zip` | SenseVoice Small(含 fsmn-vad) | zh/en/ja/ko/yue,快 |
| `stt-whisper-tiny.zip` | faster-whisper tiny | 最小最快,精度低 |
| `stt-whisper-base.zip` | faster-whisper base | 轻量 |
| `stt-whisper-small.zip` | faster-whisper small | 均衡 |
| `stt-whisper-large-v3-turbo.zip` | faster-whisper large-v3-turbo | 多语言,推荐 |
| `stt-whisper-large-v2.zip` | faster-whisper large-v2 | 高精度,大 |
| `stt-anime-whisper.zip` | Anime-Whisper | 日语动漫/表现力强 |
| `stt-qwen3-asr-0.6b.zip` | Qwen3-ASR 0.6B | 多语言 |
| `stt-qwen3-asr-1.7b.zip` | Qwen3-ASR 1.7B | 最准,最大 |

### 图片 OCR(图片翻译)
| zip | 说明 |
|---|---|
| `ocr-tiny.zip` | PP-OCRv6 tiny(最快) |
| `ocr-small.zip` | PP-OCRv6 small(默认) |
| `ocr-medium.zip` | PP-OCRv6 medium(最准) |

> 每个 OCR zip 已含检测+识别+文字方向模型,自成一套。

### PDF / 图片去字
| zip | 说明 |
|---|---|
| `pdf-doclayout.zip` | BabelDOC 版面模型(PDF 翻译) |
| `image-inpaint-lama.zip` | LaMa(擦除图片原文,可选) |

## 说明 / Notes
- 校验和见 `SHA256SUMS.txt`。
- 一个 zip = 一个可用单元(依赖已打包在内,例如 SenseVoice 自带 VAD、每个 OCR 自带方向模型)。
- 不下模型也能用 API 文本/文档/字幕翻译;模型只对 PDF 版面、图片 OCR、语音转文字这些本地功能需要。
