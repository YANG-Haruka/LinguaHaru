<div align="center">
  <img src="img/ico.png" alt="LinguaHaru" id="title" style="height: 200px; width: auto;" />

[English](README.md) | 简体中文 | [日本語](README_JP.md)  
<br/><a href="https://github.com/YANG-Haruka/LinguaHaru/wiki" target="_blank">📚 使用说明 Wiki</a>


<div align=center><img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru"/></div>
<p align='center'>次世代AI翻译神器，一键高质精准翻译各类常用文件</p>
<h3 align='center'>支持的文件格式</h3>
<p align='center'><b>📄 DOCX</b> • <b>📊 XLSX</b> • <b>📑 PPTX</b> • <b>📰 PDF</b> • <b>📝 TXT</b> • <b>🎬 SRT</b> • <b>📘 MD</b></p>

</div>
<h2 id="What's This">这是什么？</h2>
这款基于最前沿大语言模型的翻译工具，以极简操作提供卓越翻译质量，支持多种文档格式与语言。

它提供以下功能：

- 多格式兼容：完美支持 .docx、.pptx、.xlsx、.pdf、.txt、.srt 等常见文件格式，未来将拓展更多文档类型。
- 全球语言互译：覆盖中/英/日/韩/俄等10+语言，持续扩展，满足全球化需求。
- 一键极速翻译：无需繁琐操作，上传文件点击翻译即刻生成精准翻译。
- 灵活翻译引擎：自由切换本地模型（Ollama）与在线API（Deepseek/OpenAI等），随时适配不同使用环境。
- 局域网共享：一台主机，即可在本地网络内所有设备轻松使用，高效协同办公。


<h2 id="install">安装和使用</h2>

1. [CUDA](https://developer.nvidia.com/cuda-downloads)   
您需要安装CUDA（目前11.7和12.1测试没有问题）  

2. Python (python==3.10)  
    建议使用[Conda](https://www.anaconda.com/download)创建虚拟环境  
    ```bash
    conda create -n lingua-haru python=3.10
    conda activate lingua-haru
    ```

3. 安装依赖
    - 依赖包
        ```bash
        pip install -r requirements.txt
        ```
    - 模型下载 
        下载后请保存在"models"文件夹中**  
        - [夸克网盘](https://pan.quark.cn/s/c4ba89fa263c#/list/share)
        - [Google Drive](https://drive.google.com/file/d/1STq_eBNHAMzLxvkM5-2fdepa9sCX9WyJ/view?usp=sharing)


4. 运行工具
    ```bash
    python app.py
    ```
    默认访问地址为
    ```bash
    http://127.0.0.1:9980
    ```

5. 本地大语言模型支持  
    目前仅支持[Ollama](https://ollama.com/)  
    您需要下载Ollama依赖和用于翻译的模型
    - 下载模型（推荐QWen系列模型）
        ```bash
        ollama pull qwen2.5
        ```

<h2 id="preview">预览</h2>
<div align="center">
  <img src="img/sample.gif" width="80%"/>
</div>


## 参考项目
- [ollama-python](https://github.com/ollama/ollama-python)
- [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)

## 待办事项
- 添加继续翻译功能。

## 更新日志
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