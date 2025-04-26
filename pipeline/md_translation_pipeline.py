import json
import os
import re
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_md_content_to_json(file_path):
    """
    提取Markdown文件中的所有文本内容并以JSON格式保存
    处理复杂的HTML标签结构
    保持行格式和文档结构
    """
    # 初始化数据结构
    content_data = []     # 要翻译的内容
    structure_items = []  # 完整的文档结构
    position_index = 0    # 追踪文档中的位置
    
    # 读取MD文件内容
    with open(file_path, 'r', encoding='utf-8') as md_file:
        content = md_file.read()
        
    # 保存原始内容
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    with open(os.path.join(temp_folder, "original_content.md"), "w", encoding="utf-8") as original_file:
        original_file.write(content)
    
    # 将内容按行分割
    lines = content.split('\n')
    
    # 计数器
    count = 0
    
    # 用于跟踪代码块
    in_code_block = False
    
    # 处理每一行
    for line_index, line in enumerate(lines):
        # 处理代码块
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            structure_items.append({
                "index": position_index,
                "type": "code_marker",
                "value": line,
                "translate": False
            })
            position_index += 1
            continue
        
        # 在代码块内的内容不翻译
        if in_code_block:
            structure_items.append({
                "index": position_index,
                "type": "code_content",
                "value": line,
                "translate": False
            })
            position_index += 1
            continue
            
        # 检查是否为空行
        if not line.strip():
            structure_items.append({
                "index": position_index,
                "type": "empty_line",
                "value": line,
                "translate": False
            })
            position_index += 1
            continue
            
        # 决定行处理策略
        if line.strip().startswith('<') and '>' in line:
            # 检查是否是单独的HTML标签，没有需要翻译的内容
            if line.count('<') == line.count('>') and re.match(r'^<[^>]*>$', line.strip()):
                # 纯HTML标签，不需要翻译
                structure_items.append({
                    "index": position_index,
                    "type": "html_tag_only",
                    "value": line,
                    "translate": False
                })
                position_index += 1
                continue
                
            # 检查是否为HTML注释
            if '<!--' in line and '-->' in line:
                structure_items.append({
                    "index": position_index,
                    "type": "html_comment",
                    "value": line,
                    "translate": False
                })
                position_index += 1
                continue
                
            # 处理包含简单内容的HTML标签 (如 <h1>Title</h1>)
            simple_pattern = r'^<([a-zA-Z0-9]+)[^>]*>(.*?)</\1>$'
            simple_match = re.match(simple_pattern, line.strip())
            
            if simple_match and should_translate(simple_match.group(2)):
                tag_name = simple_match.group(1)
                content_text = simple_match.group(2)
                
                # 提取开始标签和结束标签
                opening_tag = line[:line.find('>') + 1]
                closing_tag = line[line.rfind('<'):]
                
                count += 1
                structure_items.append({
                    "index": position_index,
                    "type": "html_simple",
                    "opening_tag": opening_tag,
                    "content": content_text,
                    "closing_tag": closing_tag,
                    "value": line,
                    "translate": True,
                    "count": count
                })
                
                content_data.append({
                    "count": count,
                    "index": position_index,
                    "type": "html_content",
                    "value": content_text
                })
                position_index += 1
                continue
                
            # 处理复杂的HTML结构 (如包含多个标签的段落)
            # 例如: <p align='center'><b>DOCX</b> • <b>XLSX</b> • ...</p>
            
            # 首先检查是否为完整的HTML结构 (开始和结束标签匹配)
            complex_pattern = r'^<([a-zA-Z0-9]+)[^>]*>(.*)</\1>$'
            complex_match = re.match(complex_pattern, line.strip())
            
            if complex_match:
                outer_tag = complex_match.group(1)
                inner_content = complex_match.group(2)
                
                # 提取最外层标签
                opening_outer_tag = line[:line.find('>') + 1]
                closing_outer_tag = line[line.rfind('<'):]
                
                # 检查内容是否需要翻译
                if should_translate(inner_content):
                    count += 1
                    structure_items.append({
                        "index": position_index,
                        "type": "html_complex",
                        "opening_tag": opening_outer_tag,
                        "content": inner_content,
                        "closing_tag": closing_outer_tag,
                        "value": line,
                        "translate": True,
                        "count": count
                    })
                    
                    content_data.append({
                        "count": count,
                        "index": position_index,
                        "type": "html_complex_content",
                        "value": inner_content
                    })
                else:
                    structure_items.append({
                        "index": position_index,
                        "type": "html_preserved",
                        "value": line,
                        "translate": False
                    })
                position_index += 1
                continue
                
            # 对于无法识别模式的HTML，保留原样
            structure_items.append({
                "index": position_index,
                "type": "html_unknown",
                "value": line,
                "translate": False
            })
            position_index += 1
            continue
            
        # 处理普通文本行
        if should_translate(line):
            count += 1
            structure_items.append({
                "index": position_index,
                "type": "text",
                "value": line,
                "translate": True,
                "count": count
            })
            
            content_data.append({
                "count": count,
                "index": position_index,
                "type": "text",
                "value": line
            })
        else:
            # 不需要翻译的其他内容
            structure_items.append({
                "index": position_index,
                "type": "non_translatable",
                "value": line,
                "translate": False
            })
        
        position_index += 1
    
    # 保存完整的文档结构
    structure_path = os.path.join(temp_folder, "structure.json")
    with open(structure_path, "w", encoding="utf-8") as structure_file:
        json.dump(structure_items, structure_file, ensure_ascii=False, indent=4)
    
    # 保存需要翻译的内容
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)
    
    app_logger.info(f"Markdown content extracted to: {json_path}, total {count} lines to translate")
    return json_path

def write_translated_content_to_md(file_path, original_json_path, translated_json_path):
    """
    将翻译后的内容写入新的Markdown文件，保持原始HTML结构
    """
    # 获取文件路径
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    
    # 加载文档结构
    structure_path = os.path.join(temp_folder, "structure.json")
    with open(structure_path, "r", encoding="utf-8") as structure_file:
        structure_items = json.load(structure_file)
    
    # 加载翻译结果
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)
    
    # 创建翻译映射 (count -> 翻译文本)
    translations = {}
    for item in translated_data:
        count = item.get("count")
        if count:
            translations[count] = item.get("translated", "")
    
    # 重建文档
    final_lines = []
    
    for item in structure_items:
        if not item.get("translate", False):
            # 不需要翻译的内容，直接使用原始值
            final_lines.append(item["value"])
        else:
            # 需要翻译的内容
            count = item.get("count")
            if count in translations:
                if item["type"] in ["html_simple", "html_complex"]:
                    # 重建HTML标签和翻译内容
                    final_lines.append(
                        item["opening_tag"] + 
                        translations[count] + 
                        item["closing_tag"]
                    )
                else:
                    # 普通文本
                    final_lines.append(translations[count])
            else:
                # 没有找到翻译，使用原值
                final_lines.append(item["value"])
    
    # 将所有行连接为最终文档
    final_content = '\n'.join(final_lines)
    
    # 创建输出文件
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.md")
    
    # 写入最终翻译内容
    with open(result_path, "w", encoding="utf-8") as result_file:
        result_file.write(final_content)
    
    app_logger.info(f"Translated Markdown document saved to: {result_path}")
    return result_path