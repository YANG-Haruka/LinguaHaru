<div align="center">
  <img src="img/ico.png" alt="LinguaHaru" id="title" style="height: 200px; width: auto;" />


[English](README.md) | [简体中文](README_ZH.md) | 日本語


<div align=center><img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru"/></div>
<p align='center'>ワンクリックで様々な一般的なファイル形式に対して高品質で正確な翻訳を提供する次世代AI翻訳ツール</p>
<h3 align='center'>対応ファイル形式</h3>
<p align='center'><b>📄 DOCX</b> • <b>📊 XLSX</b> • <b>📑 PPTX</b> • <b>📰 PDF</b> • <b>📝 TXT</b> • <b>🎬 SRT</b> • <b>📘 MD</b></p>

</div>
<h2 id="What's This">これは何ですか？</h2>
最先端の大規模言語モデルに基づいたこの翻訳ツールは、シンプルな操作で優れた翻訳品質を提供し、多様な文書形式と言語をサポートしています。

以下の機能を提供します：

- 多形式互換性：.docx、.pptx、.xlsx、.pdf、.txt、.srtなどの一般的なファイル形式を完全にサポートし、将来的にはさらに多くの文書タイプに拡張予定。
- グローバル言語翻訳：中国語/英語/日本語/韓国語/ロシア語など10以上の言語をカバーし、グローバル化のニーズを満たすために継続的に拡張中。
- ワンクリック高速翻訳：複雑な操作は不要、ファイルをアップロードして翻訳をクリックするだけで、即座に正確な翻訳を生成。
- 柔軟な翻訳エンジン：ローカルモデル（Ollama）とオンラインAPI（Deepseek/OpenAIなど）を自由に切り替え、異なる使用環境にいつでも適応可能。
- LAN共有：1台のホストコンピュータで、ローカルネットワーク内のすべてのデバイスが簡単に使用でき、効率的な共同作業が可能。


<h2 id="install">インストールと使用方法</h2>

1. [CUDA](https://developer.nvidia.com/cuda-downloads)   
CUDAをインストールする必要があります（現在11.7と12.1でテスト済みで問題なし）  

2. Python (python==3.10)  
    [Conda](https://www.anaconda.com/download)を使用して仮想環境を作成することをお勧めします  
    ```bash
    conda create -n lingua-haru python=3.10
    conda activate lingua-haru
    ```

3. 依存関係のインストール
    - 依存パッケージ
        ```bash
        pip install -r requirements.txt
        ```
    - モデルのダウンロード 
        **ダウンロード後、「models」フォルダに保存してください**  
        - [Quark Cloud](https://pan.quark.cn/s/c4ba89fa263c#/list/share)
        - [Google Drive](https://drive.google.com/file/d/1STq_eBNHAMzLxvkM5-2fdepa9sCX9WyJ/view?usp=sharing)


4. ツールの実行
    ```bash
    python app.py
    ```
    デフォルトのアクセスアドレスは
    ```bash
    http://127.0.0.1:9980
    ```

5. ローカル大規模言語モデルのサポート  
    現在は[Ollama](https://ollama.com/)のみをサポート  
    Ollamaの依存関係と翻訳用のモデルをダウンロードする必要があります
    - モデルのダウンロード（QWenシリーズモデルを推奨）
        ```bash
        ollama pull qwen2.5
        ```

<h2 id="preview">プレビュー</h2>
<div align="center">
  <img src="img/sample.gif" width="80%"/>
</div>


## 参考プロジェクト
- [ollama-python](https://github.com/ollama/ollama-python)
- [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)

## 今後の予定
- 翻訳継続機能の追加。

## 更新履歴
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
ソフトウェアコードは完全にオープンソースであり、GPL-3.0ライセンスの下で自由に使用できます。  
このソフトウェアはAI翻訳サービスのみを提供し、このソフトウェアを使用して翻訳されたコンテンツは、その作成者とは関係ありません。  
ユーザーは法律を遵守し、合法的な翻訳活動を行うべきです。