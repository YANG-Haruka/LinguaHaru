import json
import os
import re
from bs4 import BeautifulSoup  # Import BeautifulSoup for HTML parsing
from .skip_pipeline import should_translate
from config.log_config import app_logger

def is_base64_image(text):
    """
    Check if text is base64 encoded image
    """
    # Check for base64 image patterns
    base64_image_patterns = [
        r'data:image/[^;]+;base64,',  # data:image/png;base64,
        r'!\[.*?\]\(data:image/',     # markdown syntax ![](data:image/...)
        r'<img[^>]*src="data:image/', # HTML img tag
    ]
    
    for pattern in base64_image_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    # Check for pure base64 string (length > 100 and only base64 chars)
    if len(text) > 100:
        # Base64 character set
        base64_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
        # Remove whitespace and check
        clean_text = ''.join(text.split())
        if len(clean_text) > 100 and all(c in base64_chars for c in clean_text):
            # Check base64 length rule
            if len(clean_text) % 4 == 0:  # base64 length must be multiple of 4
                return True
    
    return False

def should_translate_enhanced(text):
    """
    Enhanced translation check, exclude base64 images
    """
    # First check if it's base64 image
    if is_base64_image(text):
        return False
    
    # Then use original check logic
    return should_translate(text)

def extract_md_content_to_json(file_path):
    """
    Extract Markdown content to JSON, handling complex HTML structures including nested tables
    Preserves line formats and document structure
    Enhanced to skip base64 image content
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
        
        # Check if line is base64 image
        if is_base64_image(line):
            structure_items.append({
                "index": position_index,
                "type": "base64_image",
                "value": line,
                "translate": False
            })
            position_index += 1
            app_logger.info(f"Skipping base64 image at line {line_index + 1}")
            continue
            
        # Process HTML tags
        if line.strip().startswith('<') and '>' in line:
            
            # Handle complex HTML tables and other nested structures
            if ('<html>' in line.lower() or '<table>' in line.lower()) and ('</html>' in line.lower() or '</table>' in line.lower()):
                try:
                    # Parse HTML using BeautifulSoup
                    soup = BeautifulSoup(line, 'html.parser')
                    
                    # Track cells that need translation
                    translatable_cells = []
                    
                    # Find all table cells
                    for i, td in enumerate(soup.find_all('td')):
                        # Get the text content of the cell
                        cell_text = td.get_text().strip()
                        
                        # Check if cell needs translation (with base64 check)
                        if cell_text and should_translate_enhanced(cell_text):
                            count += 1
                            # Store information about translatable cell
                            translatable_cells.append({
                                "count_src": count,
                                "index": i,
                                "original_text": cell_text
                            })
                            
                            # Add to content data for translation
                            content_data.append({
                                "count_src": count,
                                "index": position_index,
                                "type": "html_table_cell",
                                "value": cell_text
                            })
                    
                    # If we found any cells to translate
                    if translatable_cells:
                        structure_items.append({
                            "index": position_index,
                            "type": "html_table",
                            "value": line,
                            "translate": True,
                            "translatable_cells": translatable_cells
                        })
                    else:
                        # No cells need translation
                        structure_items.append({
                            "index": position_index,
                            "type": "html_preserved",
                            "value": line,
                            "translate": False
                        })
                    
                    position_index += 1
                    continue
                except Exception as e:
                    # Log parsing error and fall back to default handling
                    app_logger.error(f"Error parsing HTML table: {str(e)}")

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
            
            if simple_match and should_translate_enhanced(simple_match.group(2)):
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
                    "count_src": count
                })
                
                content_data.append({
                    "count_src": count,
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
                
                # Check if content needs translation (with base64 check)
                if should_translate_enhanced(inner_content):
                    count += 1
                    structure_items.append({
                        "index": position_index,
                        "type": "html_complex",
                        "opening_tag": opening_outer_tag,
                        "content": inner_content,
                        "closing_tag": closing_outer_tag,
                        "value": line,
                        "translate": True,
                        "count_src": count
                    })
                    
                    content_data.append({
                        "count_src": count,
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
            
        # Handle regular text (with base64 check)
        if should_translate_enhanced(line):
            count += 1
            structure_items.append({
                "index": position_index,
                "type": "text",
                "value": line,
                "translate": True,
                "count_src": count
            })
            
            content_data.append({
                "count_src": count,
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
    Enhanced to handle complex HTML tables and base64 images
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
        count = item.get("count_src")
        if count:
            translations[count] = item.get("translated", "")
    
    # Rebuild document
    final_lines = []
    
    for item in structure_items:
        if not item.get("translate", False):
            # Keep original content for non-translated items (includes base64 images)
            final_lines.append(item["value"])
        else:
            # Insert translations
            if item["type"] == "html_table":
                try:
                    # Parse the original HTML
                    soup = BeautifulSoup(item["value"], 'html.parser')
                    
                    # Get all table cells
                    all_tds = soup.find_all('td')
                    
                    # Replace content in cells that need translation
                    for cell_info in item.get("translatable_cells", []):
                        cell_count = cell_info.get("count_src")
                        cell_index = cell_info.get("index")
                        
                        if cell_count in translations and cell_index < len(all_tds):
                            # Replace the text content while preserving HTML structure
                            # This maintains attributes and nested elements
                            current_cell = all_tds[cell_index]
                            
                            # If the cell has children elements, we need to be careful
                            if list(current_cell.children) and not all(isinstance(c, str) for c in current_cell.children):
                                # Complex cell with nested elements - log this case
                                app_logger.warning(f"Complex cell structure at index {cell_index}, translation may be incomplete")
                                # Simple approach: replace text nodes only
                                for text_node in current_cell.find_all(text=True, recursive=True):
                                    if text_node.strip() == cell_info.get("original_text"):
                                        text_node.replace_with(translations[cell_count])
                            else:
                                # Simple text content - straightforward replacement
                                current_cell.string = translations[cell_count]
                    
                    # Add the modified HTML to the final document
                    final_lines.append(str(soup))
                except Exception as e:
                    # Log error and fall back to original content
                    app_logger.error(f"Error rebuilding HTML table: {str(e)}")
                    final_lines.append(item["value"])
            elif item["type"] in ["html_simple", "html_complex"]:
                # Standard handling for simple and complex HTML
                count = item.get("count_src")
                if count in translations:
                    final_lines.append(
                        item["opening_tag"] + 
                        translations[count] + 
                        item["closing_tag"]
                    )
                else:
                    final_lines.append(item["value"])
            else:
                # Regular text
                count = item.get("count_src")
                if count in translations:
                    final_lines.append(translations[count])
                else:
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