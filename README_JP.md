# AI-Office-Translator

**PyQt-Fluent-Widgetsを基にしたUI開発中...**  
**100件以上のファイルでテスト済み**  
**このプロジェクトが役に立った場合、ぜひStarをクリックしてください ^ ^_**

## What's this
これは、**無料**、**完全ローカル**、**ユーザーフレンドリー**な翻訳ツールです。Word、PowerPoint、ExcelなどのOfficeファイルを異なる言語間で翻訳するのに役立ちます。  
主な機能は以下の通りです：
  
- 対応ファイル形式：.docx、.pptx、.xlsxファイルをサポート  
- 言語選択：英語、中国語、日本語の間で翻訳が可能

## クイックスタート
### CUDA
CUDAをインストールする必要があります  
（現在、バージョン11.7および12.1で問題なく動作することを確認済み）

### Ollama
Ollama依存関係および翻訳用モデルをダウンロードする必要があります  
- Ollamaのダウンロード  
https://ollama.com/  

- モデルのダウンロード（推奨：QWenシリーズモデル）  
```bash
ollama pull qwen2.5
```
### 仮想環境（任意）
仮想環境を作成して有効化
```bash
conda create -n ai-translator python=3.10
conda activate ai-translator
```
### 必要な依存関係をインストール
```bash
pip install -r requirements.txt
```
### ツールを起動
```bash
python app.py
```

## アプリケーション
### 使用方法
![APP](img/app.png)

- 言語選択   
ソース言語（元のファイルの言語）とターゲット言語（翻訳先の言語）を選択。  
- モデル選択   
Model欄でOllamaからダウンロードしたモデルを選択。Max_tokensの設定は変更しないことを推奨（LLMに詳しい場合を除く）。  
- ファイルをアップロード  
「Upload Office File」をクリック、または指定エリアにドラッグ＆ドロップして翻訳したいファイルをアップロード。プログラムが自動的にファイル形式を認識します。  
- 翻訳開始  
「Translate」ボタンをクリックすると翻訳が開始されます。  
- ファイルをダウンロード   
翻訳完了後、「Download Translated File」から翻訳されたファイルをダウンロード可能。また、翻訳結果は~/resultフォルダにも保存されます。   
![APP](img/app_online.png)
オンラインモード追加済み、現在はDeepseek-v3のみ対応（低コスト/高速->0.1元/100万tokens） 
オンラインモードを有効にすると、API-KEYが必要です。公式サイトで取得してください：
https://www.deepseek.com/
![APP](img/app_completed.png)
翻訳完了後、ダウンロードボックスが表示されます。  

### サンプル
- Excelファイル：英語から日本語  
![excel_sample](img/excel.png)  
- PPTファイル：英語から日本語  
![ppt_sample](img/ppt.png)  
- Wordファイル：英語から日本語  
![word_sample](img/word.png)
- PDFファイル：英語から日本語  
![pdf_sample](img/pdf.png)


デフォルトのアクセスURL：
```bash
http://127.0.0.1:9980
```
ローカルネットワークで共有する場合は、最後の行を修正してください：
```bash
iface.launch(share=True)
```

## 参考プロジェクト
- [ollama-python](https://github.com/ollama/ollama-python)
- [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)

## 今後の更新予定
- より多くのモデルおよびファイル形式をサポート

## ソフトウェア声明
本ソフトウェアは完全オープンソースで、自由に使用できます。GPL-3.0ライセンスに従ってください。
ソフトウェアはAI翻訳サービスのみを提供し、生成された翻訳コンテンツに関する責任を制作者は負いません。法律を守り、合法的に翻訳をご利用ください。
Qwenモデルに関する声明：
コードおよびモデルウェイトは学術研究用途に完全に開放されており、商用利用も可能です。詳細は通義千問LICENSEをご覧ください。

## 更新履歴
- 2025/02/01  
翻訳失敗テキストのロジックを更新しました。
- 2025/01/15
PDF翻訳のバグ修正、多言語サポート追加、小猫ちゃんを撫でました。  
- 2025/01/11
PDFサポートを追加。PDFMathTranslateを参考にしました。  
- 2025/01/10
Deepseek-v3のサポートを追加（PC性能が低い方におすすめ、コスパ最強モデル）。ローカルモデルと比較して翻訳品質が向上。  
API取得：https://www.deepseek.com/
- 2025/01/03
新年おめでとうございます！ロジックを再構築し、校正機能とログ記録を追加。  
- 2024/12/16
エラーチェックと再翻訳機能を更新。  
- 2024/12/15
検証機能を追加、コンテキスト取得のバグを修正。  
- 2024/12/12
改行処理を更新、一部のエラーを修正。  