<div align="center">
<h1 id="title">LinguaHaru</h1>

[English](README.md) | 简体中文 | [日本語](README_JP.md) 


<div align=center><img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru"/></div>
<p align='center'>一款用户友好的AI翻译工具，支持一键翻译多种文档格式和语言。</p>

</div>
<h2 id="What's This">这是什么？</h2>
这款基于大语言模型的次世代翻译工具，旨在使用最简单的方式提供最高质量的翻译结果。支持多种文档格式和语言。

它提供以下功能：

- 支持的文件类型：包括.docx、.pptx、.xlsx、.pdf、.txt和.srt文件，未来将添加更多格式。
- 语言选项：支持10多种语言之间的翻译，并计划进一步扩展。
- 一键翻译：轻松实现文档的一键翻译。
- 灵活的翻译模型：支持本地模型（Ollama）和在线API调用（Deepseek/OpenAI等）。
- 局域网共享：一台主机作为终端，在本地网络各设备均可使用。


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
        - [百度网盘](https://pan.baidu.com/s/1erFEqR4CgR0JwWvpvms4eQ?pwd=v813)
        - [Google Drive](https://drive.google.com/file/d/1UVfJhpxWywBu250Xt-TDkvN5Jjjj0LN7/view?usp=sharing)


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
  <h3>Excel</h3>
  <img src="img/excel.png" width="80%"/>
  <h3>PPT</h3>
  <img src="img/ppt.png" width="80%"/>
  <h3>PDF</h3>
  <img src="img/pdf.png" width="80%"/>
</div>


## 参考项目
- [ollama-python](https://github.com/ollama/ollama-python)
- [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)

## 待办事项
- 添加继续翻译功能。
- 优化Excel文件的翻译速度。
- Word Excel等双语对照

## 更新日志
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
- 2024/12/16  
更新错误检测和重新翻译功能
- 2024/12/15  
添加了一些验证并修复了获取上下文功能的bug
- 2024/12/12  
更新了换行符的处理。修复了一些bug

## 软件免责声明  
软件代码完全开源，可按GPL-3.0许可证自由使用。  
该软件仅提供AI翻译服务，使用本软件翻译的任何内容与其创建者无关。  
用户应遵守法律并进行合法的翻译活动。

千问模型免责声明  
代码和模型权重完全开放用于学术研究，并支持商业使用。  
有关特定开源协议的详细信息，请参阅千问LICENSE。