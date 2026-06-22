<div align="center">
  <img src="assets/img/ico.png" alt="LinguaHaru" id="title" style="height: 200px; width: auto;" />


[English](README.md) | [简体中文](README_ZH.md) | 日本語
<br/><a href="https://github.com/YANG-Haruka/LinguaHaru/wiki/jp-Home" target="_blank">📚 使用方法ガイド（Wiki）</a>

<div align=center><img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru"/></div>
<p align='center'>ワンクリックで様々な一般的なファイル形式に対して高品質で正確な翻訳を提供する次世代AI翻訳ツール</p>
<h3 align='center'>対応ファイル形式</h3>
<p align='center'><b>📄 DOCX</b> • <b>📊 XLSX</b> • <b>📑 PPTX</b> • <b>📰 PDF</b> • <b>📝 TXT</b> • <b>🎬 SRT/ASS/VTT/LRC</b> • <b>📘 MD</b> • <b>📚 EPUB</b> • <b>🗂 CSV/TSV</b> • <b>🌐 HTML</b> • <b>📃 ODT</b> • <b>🔤 JSON</b></p>

</div>
<h2 id="What's This">これは何ですか？</h2>
最先端の大規模言語モデルに基づいたこの翻訳ツールは、シンプルな操作で優れた翻訳品質を提供し、多様な文書形式と言語をサポートしています。

以下の機能を提供します：

- **2つのフロントエンド**：Web UI（ブラウザ）とデスクトップ（Qt Fluent Design）。同一バックエンド、お好みで選択。
- **多形式対応**：.docx / .pptx / .xlsx / .pdf / .txt / 字幕(srt/ass/vtt/lrc) / .md / .epub / csv / html / odt / json、画像・動画/音声も。
- **多言語翻訳**：中/英/日/韓/露など 13+ 言語。UI も多言語。
- **ワンクリック翻訳**：ドラッグするだけ。翻訳モード（精確/汎用）・用語集・対訳出力・形式別オプション対応。
- **ホーム「翻訳」**：Google 翻訳風の短文クイック翻訳。音声入力・読み上げ対応。
- **リアルタイム音声**：話しながら翻訳。自動文区切り、原文と訳文をライブ表示。
- **プラグイン + マーケット**：PDF・画像 OCR・動画/音声字幕・リアルタイム音声・音声入力はオプションのプラグイン。必要な時だけインストール（uv 同梱で高速）。リモートマーケットからサードパーティ製の自己完結プラグインも本体更新なしで追加可能。
- **スマート更新**（ポータブル版）：ワンクリックで本体を更新し、導入済みプラグイン・モデル・設定を保持。
- **初回ガイド**：各ページを案内するインタラクティブなスポットライトツアー。
- **柔軟なエンジン**：オンライン API（DeepSeek / OpenAI など）とローカル（Ollama / LM Studio）を自由に切替。
- **中国本土対応**：HuggingFace / PyPI / GitHub は公式を優先探索し、不通時はミラー（hf-mirror / 清華 / ghproxy）へ自動切替。
- **LAN 共有**（Web 版のみ）：1 台のホストを LAN 内の各デバイスで共用。


<h2 id="install">インストールと使用方法</h2>

### 方法A（推奨）：ポータブル版 — 解凍してすぐ実行

Python も CUDA も不要。ダウンロードして解凍、ダブルクリックするだけ。

1. [Releases](https://github.com/YANG-Haruka/LinguaHaru/releases/latest) からダウンロード：
    - `LinguaHaru-web.zip` — Web 版（ブラウザ）
    - `LinguaHaru-desktop.zip` — デスクトップ版（Qt）
2. 任意の場所に解凍。
3. 起動：
    - `Start-Web.bat` — **ブラウザを自動で開きます**（URL 入力不要）
    - `Start-Desktop.bat` — ネイティブウィンドウ
4. 「インターフェース管理」で接口（例：DeepSeek）を追加し、API キーを入力、カードをクリックして有効化。
5. PDF / 画像 OCR / 動画字幕 / リアルタイム音声が必要なら、「プラグイン」ページで**必要な時にインストール**（uv 同梱で高速、本土ではミラー自動使用）。
6. モデルは「プラグイン」ページから、または[モデル説明](docs/MODELS.md)に従いネットディスクから個別にダウンロードし、アプリの `models/` フォルダへ解凍。

> 新バージョンが出たら、ポータブル版は**スマート更新**でその場で更新し、導入済みプラグイン・モデル・設定を保持します。

### 方法B：ソースから実行（開発 / 上級）

1. Python 3.12（[Conda](https://www.anaconda.com/download) 仮想環境を推奨）
    ```bash
    conda create -n lingua-haru python=3.12 && conda activate lingua-haru
    ```
2. コア依存 + フロントエンドを1つ
    ```bash
    pip install -r requirements/base.txt
    pip install -r requirements/web.txt   # Web:     python -m webapp.server  (http://127.0.0.1:8080)
    pip install -r requirements/qt.txt    # Desktop: python app_qt.py
    ```
3. オプションプラグイン（UI「プラグイン」ページからも可）
    ```bash
    pip install -r plugins/pdf/requirements.txt       # PDF（BabelDOC、レイアウト保持）
    pip install -r plugins/ocr/requirements.txt       # 画像 OCR
    pip install -r plugins/video/requirements.txt     # 動画/音声字幕（ffmpeg 同梱）+ リアルタイム音声
    pip install -r plugins/speechio/requirements.txt  # 翻訳ページの音声入力 + 読み上げ
    # モデルは初回使用時に models/ へダウンロード（GPU 文字起こしには CUDA 版 torch が必要）。
    ```

### ローカル LLM（任意）
オンライン API のほか、ローカルの [Ollama](https://ollama.com/) / LM Studio に対応。例：`ollama pull qwen2.5`、その後「インターフェース管理」でローカル接口を有効化。

<h2 id="preview">プレビュー</h2>
<div align="center">
  <img src="assets/img/sample.gif" width="80%"/>
</div>


## 参考プロジェクト
- [ollama-python](https://github.com/ollama/ollama-python)
- [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)

## 今後の予定
- 翻訳継続機能の追加。

## 更新履歴
- 2026/06
**V5.1 更新**：新しい**ポータブル版**（Python 内蔵、解凍してすぐ実行）；**プラグインマーケット**（必要に応じてインストール + サードパーティ製プラグインのリモート取得）；**スマート更新**（プラグイン/モデル/設定を保持したままワンクリック更新）；初回の**オンボーディングツアー**；Web 版は**ブラウザを自動起動**；モデルを**個別にネットディスクから配布可能**；中国本土ミラー自動切替（HF/PyPI/GitHub）；翻訳品質と安定性の多数の修正。
- 2026/01/28
V5.0 更新：PDFライブラリを更新。UIインターフェースを最適化。より多くの実用的な機能を追加。一年間のご愛顧に感謝します！
- 2025/05/09
V3.0 更新：マルチスレッド対応と翻訳継続機能を追加。Markdownファイルの翻訳機能を追加。Qwen3シリーズのサポートを強化。ログ表示を最適化。
- 2025/04/02  
バージョン v2.3 に更新し、IconやTitleの設定を追加。マルチタスクキューに対応。翻訳結果の検出ロジックを最適化。翻訳結果と原文を比較表示する機能を追加。
- 2025/03/14
V2.0にアップデート、Txtファイルのサポートを追加。Word/Excel/長文テキストの翻訳を最適化。カスタマイズ可能なリトライ回数機能を追加。翻訳結果の表示を改善。
- 2025/02/01  
翻訳失敗テキストの処理ロジックを更新。
- 2025/01/15  
PDF翻訳のバグを修正し、多言語サポートを追加し、子猫を撫でました。
- 2025/01/11  
PDFのサポートを追加。参考プロジェクト：[PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)
- 2025/01/10    
deepseek-v3のサポートを追加。現在APIを使用して翻訳できます（より安定）。  
API取得：https://www.deepseek.com/
- 2025/01/03  
新年おめでとう！ロジックを改訂し、レビュー機能を追加し、ログ記録を強化しました。


## ソフトウェア免責事項  
本ソフトウェアはGPL-3.0ライセンスのもとで完全にオープンソースです。自由にご利用いただけます。
本ソフトはAI翻訳サービスのみを提供しており、翻訳内容については作者に責任はありません。
どうぞ法令を遵守し、適切な形でご利用くださいませ。
もしクレジットを入れていただけたらとっても嬉しいです～♡もちろん、なくても全然大丈夫です(´︶`)ﾉ
