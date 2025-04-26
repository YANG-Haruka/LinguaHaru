import json
import os
import re
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_md_content_to_json(file_path):
    """
    Extract Markdown content to JSON, handling complex HTML structures
    Preserves line formats and document structure
    """
    # Initialize data structures
    content_data = []     # Content to translate
    structure_items = []  # Complete document structure
    position_index = 0    # Position tracker
    
    # Read file content
    with open(file_path, 'r', encoding='utf-8') as md_file:
        content = md_file.read()
        
    # Save original content
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    with open(os.path.join(temp_folder, "original_content.md"), "w", encoding="utf-8") as original_file:
        original_file.write(content)
    
    # Split content by line
    lines = content.split('\n')
    
    # Counter
    count = 0
    
    # Code block tracker
    in_code_block = False
    
    # Process each line
    for line_index, line in enumerate(lines):
        # Handle code blocks
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
        
        # Skip translation for code block content
        if in_code_block:
            structure_items.append({
                "index": position_index,
                "type": "code_content",
                "value": line,
                "translate": False
            })
            position_index += 1
            continue
            
        # Handle empty lines
        if not line.strip():
            structure_items.append({
                "index": position_index,
                "type": "empty_line",
                "value": line,
                "translate": False
            })
            position_index += 1
            continue
            
        # Process HTML tags
        if line.strip().startswith('<') and '>' in line:
            # Handle self-closing tags
            if line.count('<') == line.count('>') and re.match(r'^<[^>]*>$', line.strip()):
                structure_items.append({
                    "index": position_index,
                    "type": "html_tag_only",
                    "value": line,
                    "translate": False
                })
                position_index += 1
                continue
                
            # Handle HTML comments
            if '<!--' in line and '-->' in line:
                structure_items.append({
                    "index": position_index,
                    "type": "html_comment",
                    "value": line,
                    "translate": False
                })
                position_index += 1
                continue
                
            # Handle simple HTML tags (e.g., <h1>Title</h1>)
            simple_pattern = r'^<([a-zA-Z0-9]+)[^>]*>(.*?)</\1>$'
            simple_match = re.match(simple_pattern, line.strip())
            
            if simple_match and should_translate(simple_match.group(2)):
                tag_name = simple_match.group(1)
                content_text = simple_match.group(2)
                
                # Extract opening and closing tags
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
                
            # Handle complex HTML structures (e.g., <p><b>Text</b> • <b>More</b></p>)
            complex_pattern = r'^<([a-zA-Z0-9]+)[^>]*>(.*)</\1>$'
            complex_match = re.match(complex_pattern, line.strip())
            
            if complex_match:
                outer_tag = complex_match.group(1)
                inner_content = complex_match.group(2)
                
                # Extract outer tags
                opening_outer_tag = line[:line.find('>') + 1]
                closing_outer_tag = line[line.rfind('<'):]
                
                # Check if content needs translation
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
                
            # Preserve unrecognized HTML
            structure_items.append({
                "index": position_index,
                "type": "html_unknown",
                "value": line,
                "translate": False
            })
            position_index += 1
            continue
            
        # Handle regular text
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
            # Other non-translatable content
            structure_items.append({
                "index": position_index,
                "type": "non_translatable",
                "value": line,
                "translate": False
            })
        
        position_index += 1
    
    # Save document structure
    structure_path = os.path.join(temp_folder, "structure.json")
    with open(structure_path, "w", encoding="utf-8") as structure_file:
        json.dump(structure_items, structure_file, ensure_ascii=False, indent=4)
    
    # Save content for translation
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)
    
    app_logger.info(f"Markdown content extracted to: {json_path}, total {count} lines to translate")
    return json_path

def write_translated_content_to_md(file_path, original_json_path, translated_json_path):
    """
    Write translated content to new Markdown file while preserving HTML structure
    """
    # Get file paths
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    
    # Load document structure
    structure_path = os.path.join(temp_folder, "structure.json")
    with open(structure_path, "r", encoding="utf-8") as structure_file:
        structure_items = json.load(structure_file)
    
    # Load translation results
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)
    
    # Create translation mapping (count -> translated text)
    translations = {}
    for item in translated_data:
        count = item.get("count")
        if count:
            translations[count] = item.get("translated", "")
    
    # Rebuild document
    final_lines = []
    
    for item in structure_items:
        if not item.get("translate", False):
            # Keep original content for non-translated items
            final_lines.append(item["value"])
        else:
            # Insert translations
            count = item.get("count")
            if count in translations:
                if item["type"] in ["html_simple", "html_complex"]:
                    # Rebuild HTML with translated content
                    final_lines.append(
                        item["opening_tag"] + 
                        translations[count] + 
                        item["closing_tag"]
                    )
                else:
                    # Regular text
                    final_lines.append(translations[count])
            else:
                # Fallback to original if translation not found
                final_lines.append(item["value"])
    
    # Join lines into final document
    final_content = '\n'.join(final_lines)
    
    # Create output file
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.md")
    
    # Write final content
    with open(result_path, "w", encoding="utf-8") as result_file:
        result_file.write(final_content)
    
    app_logger.info(f"Translated Markdown document saved to: {result_path}")
    return result_path