import json
import os
import re
from lxml import etree
from zipfile import ZipFile, ZIP_DEFLATED
from .skip_pipeline import should_translate
from config.log_config import app_logger
import shutil
import tempfile
from textProcessing.text_separator import safe_convert_to_int
import datetime

def set_current_target_language(lang_code):
    """Set the current target language for date conversion decisions in bilingual processing."""
    globals()['_current_target_language'] = lang_code


# 添加日期转换配置类
class DateConversionConfig:
    """日期转换配置"""
    TARGET_LANGUAGE = 'en'  # 默认转换为英文格式
    ENABLE_AUTO_DATE_CONVERSION = True  # 启用自动日期转换

def clean_translation_brackets(text):
    """清除译文中的《》符号，保留其中的内容"""
    if not text:
        return text
    
    # 清除《》符号，但保留其中的内容
    cleaned_text = text.replace('《', '').replace('》', '')
    return cleaned_text

def detect_and_convert_untranslated_dates(original_text, translated_text, target_language='en'):
    """检测译文中未翻译的日期，并转换为目标语言格式，支持多个日期的转换"""
    # 使用全局覆盖语言（如通过前置设置 set_current_target_language 设置），若未设置则回退为传入参数
    current_lang = globals().get('_current_target_language', None)
    effective_target = current_lang if current_lang is not None else target_language

    if not DateConversionConfig.ENABLE_AUTO_DATE_CONVERSION or effective_target != 'en':
        # 如果未启用日期转换或目标语言不是英文，只清理译文中的《》符号
        return clean_translation_brackets(translated_text)
        
    # 检测原文中的日期
    original_dates = find_dates_in_text(original_text)
    
    if not original_dates:
        # 清理译文中的《》符号
        return clean_translation_brackets(translated_text)
    
    converted_text = translated_text
    conversion_count = 0
    
    # 创建日期转换映射
    date_conversions = {}
    
    # 检查每个原文日期是否在译文中仍然存在（未翻译）
    for date_info in original_dates:
        date_str = date_info['date_str']
        if date_str in converted_text:
            # 这个日期在译文中仍然存在，说明没有被翻译
            converted_date = convert_date_to_target_format(date_str, effective_target)
            if converted_date != date_str:
                date_conversions[date_str] = converted_date
                app_logger.info(f"Prepared date conversion: '{date_str}' -> '{converted_date}'")
    
    # 按日期字符串长度从长到短排序，避免短日期字符串误替换长日期的一部分
    sorted_dates = sorted(date_conversions.keys(), key=len, reverse=True)
    
    # 执行转换
    for original_date in sorted_dates:
        converted_date = date_conversions[original_date]
        
        # 计算这个日期在文本中出现的次数
        occurrences = converted_text.count(original_date)
        if occurrences > 0:
            # 替换所有出现的这个日期
            converted_text = converted_text.replace(original_date, converted_date)
            conversion_count += occurrences
            app_logger.info(f"Auto-converted {occurrences} occurrences of date: '{original_date}' -> '{converted_date}'")
    
    if conversion_count > 0:
        app_logger.info(f"Total {conversion_count} date instances auto-converted in text: '{original_text[:30]}...'")
    
    # 清理译文中的《》符号
    return clean_translation_brackets(converted_text)

def find_dates_in_text(text):
    """在文本中查找日期并返回详细信息"""
    date_patterns = [
        # 移除边界限制，支持更广泛的匹配，包括 2024-03-03 这种格式
        (r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', 'YYYY.M.D'),  # 2021.4.1, 2022-12-31, 2024-03-03
        (r'(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})', 'M.D.YYYY'),  # 4.1.2021, 12/31/2022, 03/03/2024
        (r'(\d{4})[.\-/](\d{1,2})', 'YYYY.M'),                   # 2021.4, 2022/12, 2024-03
    ]
    
    dates = []
    
    for pattern, format_type in date_patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            dates.append({
                'date_str': match.group(),
                'format_type': format_type,
                'groups': match.groups(),
                'start': match.start(),
                'end': match.end()
            })
    
    # 按位置排序，避免重复处理
    dates.sort(key=lambda x: x['start'])
    
    # 去除重叠的匹配（优先保留更长的匹配）
    filtered_dates = []
    for date in dates:
        is_overlap = False
        for i, existing_date in enumerate(filtered_dates):
            # 检查是否有重叠
            if (date['start'] < existing_date['end'] and date['end'] > existing_date['start']):
                # 有重叠，保留更长的匹配
                if len(date['date_str']) > len(existing_date['date_str']):
                    filtered_dates[i] = date  # 替换为更长的匹配
                is_overlap = True
                break
        
        if not is_overlap:
            filtered_dates.append(date)
    
    # 重新按位置排序
    filtered_dates.sort(key=lambda x: x['start'])
    return filtered_dates

def convert_date_to_target_format(date_str, target_language='en'):
    """将日期字符串转换为目标语言格式"""
    # 只有目标语言是英文时才进行转换
    if target_language != 'en':
        return date_str
        
    try:
        parsed_date = None
        is_year_month_only = False
        
        # 尝试不同的日期格式解析
        for sep in ['.', '-', '/']:
            if sep in date_str:
                parts = date_str.split(sep)
                if len(parts) == 3:
                    # 判断是 YYYY.M.D 还是 M.D.YYYY 格式
                    if len(parts[0]) == 4:  # YYYY.M.D 格式 (如 2024-03-03)
                        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                    elif len(parts[2]) == 4:  # M.D.YYYY 格式 (如 03/03/2024)
                        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
                    else:
                        continue
                    
                    # 验证日期合法性
                    if 1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100:
                        try:
                            parsed_date = datetime.datetime(year, month, day)
                            break
                        except ValueError:
                            # 处理无效日期（如2月30日）
                            continue
                        
                elif len(parts) == 2 and len(parts[0]) == 4:  # YYYY.M 格式
                    year, month = int(parts[0]), int(parts[1])
                    if 1 <= month <= 12 and 1900 <= year <= 2100:
                        parsed_date = datetime.datetime(year, month, 1)
                        is_year_month_only = True
                        break
        
        if parsed_date:
            if target_language == 'en':
                if is_year_month_only:
                    return parsed_date.strftime("%B %Y")  # March 2024
                else:
                    # 格式化为英文日期格式
                    return parsed_date.strftime("%B %d, %Y")  # March 3, 2024
            
            # 可以在这里添加其他语言的支持
            # elif target_language == 'zh':
            #     return f"{parsed_date.year}年{parsed_date.month}月{parsed_date.day}日"
        
    except (ValueError, IndexError, TypeError) as e:
        app_logger.warning(f"Failed to parse date '{date_str}': {e}")
        pass
    
    return date_str  # 如果解析失败，返回原字符串

def extract_word_content_to_json(file_path, save_temp_dir):
    """Extract translatable content from Word document to JSON - enhanced error handling"""
    temp_dir = None
    content_data = []  # 初始化在外层，确保即使出错也能访问
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    json_path = os.path.join(temp_folder, "src.json")
    
    # 添加处理状态追踪
    processing_stage = "initialization"
    
    try:
        app_logger.info(f"Starting content extraction from: {os.path.basename(file_path)}")
        
        # Create temporary directory for processing
        processing_stage = "creating_temp_directory"
        temp_dir = tempfile.mkdtemp()
        
        # Extract entire docx archive
        processing_stage = "extracting_archive"
        try:
            with ZipFile(file_path, 'r') as docx:
                docx.extractall(temp_dir)
        except Exception as e:
            app_logger.error(f"Failed to extract DOCX archive: {e}")
            raise
        
        app_logger.info("Archive extraction completed, reading XML files...")
        
        # Read main document
        processing_stage = "reading_document_xml"
        document_xml_path = os.path.join(temp_dir, 'word', 'document.xml')
        if not os.path.exists(document_xml_path):
            raise FileNotFoundError("document.xml not found in DOCX file")
            
        try:
            with open(document_xml_path, 'rb') as f:
                document_xml = f.read()
        except Exception as e:
            app_logger.error(f"Failed to read document.xml: {e}")
            raise
        
        # Read numbering.xml if exists
        processing_stage = "reading_numbering_xml"
        numbering_xml = None
        numbering_xml_path = os.path.join(temp_dir, 'word', 'numbering.xml')
        if os.path.exists(numbering_xml_path):
            try:
                with open(numbering_xml_path, 'rb') as f:
                    numbering_xml = f.read()
            except Exception as e:
                app_logger.warning(f"Failed to read numbering.xml: {e}")
        
        # Read styles.xml if exists
        processing_stage = "reading_styles_xml"
        styles_xml = None
        styles_xml_path = os.path.join(temp_dir, 'word', 'styles.xml')
        if os.path.exists(styles_xml_path):
            try:
                with open(styles_xml_path, 'rb') as f:
                    styles_xml = f.read()
            except Exception as e:
                app_logger.warning(f"Failed to read styles.xml: {e}")
        
        # Read footnotes.xml if exists
        processing_stage = "reading_footnotes_xml"
        footnotes_xml = None
        footnotes_xml_path = os.path.join(temp_dir, 'word', 'footnotes.xml')
        if os.path.exists(footnotes_xml_path):
            try:
                with open(footnotes_xml_path, 'rb') as f:
                    footnotes_xml = f.read()
                app_logger.info("Found footnotes.xml, will process footnote content")
            except Exception as e:
                app_logger.warning(f"Failed to read footnotes.xml: {e}")
        
        # Get all header and footer files
        processing_stage = "reading_header_footer_files"
        word_dir = os.path.join(temp_dir, 'word')
        header_footer_files = {}
        if os.path.exists(word_dir):
            try:
                for filename_item in os.listdir(word_dir):
                    if filename_item.startswith('header') or filename_item.startswith('footer'):
                        filepath = os.path.join(word_dir, filename_item)
                        try:
                            with open(filepath, 'rb') as f:
                                header_footer_files[f'word/{filename_item}'] = f.read()
                        except Exception as e:
                            app_logger.warning(f"Failed to read {filename_item}: {e}")
            except Exception as e:
                app_logger.warning(f"Error reading header/footer files: {e}")
        
        app_logger.info(f"Found {len(header_footer_files)} header/footer files")

        # Complete namespaces including all possible schemas
        namespaces = {
            'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
            'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
            'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
            'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
            'v': 'urn:schemas-microsoft-com:vml',
            'w10': 'urn:schemas-microsoft-com:office:word',
            'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
            'w15': 'http://schemas.microsoft.com/office/word/2012/wordml',
            'wp14': 'http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing',
            'wpc': 'http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas',
            'wpg': 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup',
            'wpi': 'http://schemas.microsoft.com/office/word/2010/wordprocessingInk',
            'wne': 'http://schemas.microsoft.com/office/word/2006/wordml',
            'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
            'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram',
            'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'
        }
        
        app_logger.info("Parsing main document XML...")
        try:
            document_tree = etree.fromstring(document_xml)
        except Exception as e:
            app_logger.error(f"Failed to parse document XML: {e}")
            raise
        
        # Parse numbering and styles information
        numbering_info = {}
        styles_info = {}
        
        if numbering_xml:
            app_logger.info("Parsing numbering information...")
            try:
                numbering_info = parse_numbering_xml(numbering_xml, namespaces)
            except Exception as e:
                app_logger.warning(f"Error parsing numbering information: {e}")
        
        if styles_xml:
            app_logger.info("Parsing styles information...")
            try:
                styles_info = parse_styles_xml(styles_xml, namespaces)
            except Exception as e:
                app_logger.warning(f"Error parsing styles information: {e}")

        item_id = 0
        
        # Extract translatable content from numbering.xml first
        if numbering_xml:
            app_logger.info("Processing numbering content...")
            try:
                numbering_items = extract_numbering_translatable_content(numbering_xml, namespaces)
                for numbering_item in numbering_items:
                    item_id += 1
                    numbering_item["id"] = item_id
                    numbering_item["count_src"] = item_id
                    content_data.append(numbering_item)
                app_logger.info(f"Extracted {len(numbering_items)} numbering items")
            except Exception as e:
                app_logger.warning(f"Error processing numbering content: {e}")
        
        # Extract footnotes content
        if footnotes_xml:
            app_logger.info("Processing footnote content...")
            try:
                footnote_items = extract_footnotes_translatable_content(footnotes_xml, namespaces)
                for footnote_item in footnote_items:
                    item_id += 1
                    footnote_item["id"] = item_id
                    footnote_item["count_src"] = item_id
                    content_data.append(footnote_item)
                app_logger.info(f"Extracted {len(footnote_items)} footnote items")
            except Exception as e:
                app_logger.warning(f"Error processing footnote content: {e}")
        
        # Process main document content
        app_logger.info("Processing main document content...")
        try:
            item_id = process_document_content(
                document_tree, content_data, item_id, numbering_info, styles_info, namespaces
            )
        except Exception as e:
            app_logger.error(f"Error processing main document content: {e}")
            # 继续处理其他内容
        
        # Process headers and footers
        if header_footer_files:
            app_logger.info(f"Processing {len(header_footer_files)} header/footer files...")
            try:
                for hf_file, hf_xml in header_footer_files.items():
                    try:
                        hf_tree = etree.fromstring(hf_xml)
                        hf_type = "header" if "header" in hf_file else "footer"
                        hf_number = os.path.basename(hf_file).split('.')[0]
                        
                        item_id = process_header_footer_content(
                            hf_tree, content_data, item_id, numbering_info, styles_info, 
                            namespaces, hf_type, hf_file, hf_number
                        )
                    except Exception as e:
                        app_logger.warning(f"Error processing {hf_file}: {e}")
                        continue
            except Exception as e:
                app_logger.warning(f"Error in header/footer processing: {e}")

        # Extract SmartArt content
        app_logger.info("Processing SmartArt content...")
        try:
            with ZipFile(file_path, 'r') as docx:
                item_id = extract_smartart_content(docx, content_data, item_id, namespaces)
        except Exception as e:
            app_logger.warning(f"Error processing SmartArt content: {e}")

        # Clear cache at the end
        _element_cache.clear()

        # Save extraction data and temp directory path
        filename = os.path.splitext(os.path.basename(file_path))[0]
        temp_folder = os.path.join(save_temp_dir, filename)
        os.makedirs(temp_folder, exist_ok=True)
        
        # Save temp directory path for later use
        temp_dir_info_path = os.path.join(temp_folder, "temp_dir_path.txt")
        with open(temp_dir_info_path, "w", encoding="utf-8") as f:
            f.write(temp_dir)
        
        json_path = os.path.join(temp_folder, "src.json")
        app_logger.info(f"Saving extracted content to: {json_path}")
        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(content_data, json_file, ensure_ascii=False, indent=4)

        app_logger.info(f"Successfully extracted {len(content_data)} content items from document: {filename}")
        return json_path
        
    except Exception as e:
        app_logger.error(f"Error during content extraction: {e}")
        # Clean up temp directory on error
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        # Clear cache on error
        _element_cache.clear()
        raise e

def extract_footnotes_translatable_content(footnotes_xml, namespaces):
    """Extract translatable content from footnotes.xml"""
    footnote_items = []
    
    if not footnotes_xml:
        return footnote_items
    
    try:
        footnotes_tree = etree.fromstring(footnotes_xml)
        
        # Get all footnotes, excluding separator and continuation separator footnotes
        footnotes = footnotes_tree.xpath('//w:footnote[not(@w:type="separator") and not(@w:type="continuationSeparator")]', namespaces=namespaces)
        
        app_logger.info(f"Found {len(footnotes)} footnotes to extract content from")
        
        for footnote in footnotes:
            footnote_id = footnote.get(f'{{{namespaces["w"]}}}id')
            if not footnote_id:
                app_logger.warning("Found footnote without ID, skipping")
                continue
            
            # Ensure footnote_id is string for consistency
            footnote_id = str(footnote_id)
            
            # Process paragraphs within the footnote
            footnote_paragraphs = footnote.xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            app_logger.debug(f"Processing footnote {footnote_id} with {len(footnote_paragraphs)} paragraphs")
            
            for para_idx, paragraph in enumerate(footnote_paragraphs):
                # Enhanced TOC detection for footnote paragraphs
                is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, False)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
                    paragraph_text = toc_title_text
                    field_info = None
                else:
                    # Extract text from paragraph including variables and formulas
                    paragraph_text, field_info = process_paragraph_element_with_formulas(paragraph, namespaces)
                    toc_structure = None
                
                if paragraph_text and paragraph_text.strip() and should_translate_enhanced(paragraph_text):
                    footnote_item = {
                        "type": "footnote",
                        "footnote_id": footnote_id,  # Ensure this is string
                        "paragraph_index": para_idx,
                        "is_toc": is_toc,
                        "value": paragraph_text.replace("\n", "␊").replace("\r", "␍"),
                        "original_pPr": extract_paragraph_properties(paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(paragraph, namespaces)
                    }
                    
                    if field_info:
                        footnote_item["field_info"] = field_info
                    
                    if is_toc:
                        footnote_item.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    footnote_items.append(footnote_item)
                    app_logger.debug(f"Extracted footnote {footnote_id}.{para_idx}: '{paragraph_text[:50]}...'")
                else:
                    app_logger.debug(f"Skipping footnote {footnote_id}.{para_idx}: empty or non-translatable content")
        
        app_logger.info(f"Extracted {len(footnote_items)} translatable footnote items")
        
    except Exception as e:
        app_logger.error(f"Error extracting footnotes content: {e}")
    
    return footnote_items

def process_paragraph_element_with_formulas(paragraph, namespaces):
    """处理包含公式的段落元素，按顺序提取所有内容"""
    result_text = ""
    formula_info = []
    field_info = []
    formula_counter = 1
    footnote_ref_counter = 1
    
    # 按顺序处理段落的所有直接子元素
    for child in paragraph:
        tag_name = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        
        if tag_name == 'oMath':  # 数学公式
            formula_placeholder = f"[formula_{formula_counter}]"
            result_text += formula_placeholder
            
            # 保存公式信息，使用字符串而不是Element对象
            formula_info.append({
                'type': 'formula',
                'placeholder': formula_placeholder,
                'formula_number': formula_counter,
                'xml_content': etree.tostring(child, encoding='unicode'),
                'position': 'paragraph_level'
            })
            formula_counter += 1
            
        elif tag_name == 'r':  # 文本运行
            # 检查是否包含脚注引用
            footnote_refs = child.xpath('.//w:footnoteReference', namespaces=namespaces)
            if footnote_refs:
                # 处理脚注引用run
                # 先处理其他内容（如果有）
                for run_child in child:
                    run_child_tag = run_child.tag.split('}')[-1]
                    if run_child_tag == 't':
                        if run_child.text:
                            result_text += run_child.text
                    elif run_child_tag == 'br':
                        result_text += "\n"
                    elif run_child_tag == 'tab':
                        result_text += "\t"
                    elif run_child_tag == 'cr':
                        result_text += "\r"
                
                # 添加脚注引用占位符
                for footnote_ref in footnote_refs:
                    footnote_id = footnote_ref.get(f'{{{namespaces["w"]}}}id')
                    if footnote_id:
                        footnote_placeholder = f"{{{{FOOTNOTE_REF_{footnote_ref_counter}}}}}"
                        result_text += footnote_placeholder
                        
                        footnote_info = {
                            'type': 'footnote_reference',
                            'placeholder': footnote_placeholder,
                            'footnote_id': footnote_id,
                            'footnote_ref_number': footnote_ref_counter,
                            'run_xml': etree.tostring(child, encoding='unicode')
                        }
                        field_info.append(footnote_info)
                        footnote_ref_counter += 1
                continue
            
            # 检查run中是否包含公式
            run_formulas = child.xpath('.//m:oMath', namespaces=namespaces)
            if run_formulas:
                # 处理包含公式的run
                run_content = process_run_with_formulas(child, namespaces, formula_counter)
                result_text += run_content['text']
                formula_info.extend(run_content['formulas'])
                formula_counter += len(run_content['formulas'])
            else:
                # 处理普通文本run
                # 检查是否为字段run
                if child.xpath('.//w:fldChar | .//w:instrText', namespaces=namespaces):
                    field_result = process_field_run(child, namespaces, 0)
                    if field_result:
                        result_text += field_result['display_text']
                        field_info.append(field_result)
                elif child.xpath('.//w:fldSimple', namespaces=namespaces):
                    field_result = process_simple_field_run(child, namespaces, 0)
                    if field_result:
                        result_text += field_result['display_text']
                        field_info.append(field_result)
                else:
                    # 普通文本run
                    for run_child in child:
                        run_child_tag = run_child.tag.split('}')[-1]
                        if run_child_tag == 't':
                            if run_child.text:
                                result_text += run_child.text
                        elif run_child_tag == 'br':
                            result_text += "\n"
                        elif run_child_tag == 'tab':
                            result_text += "\t"
                        elif run_child_tag == 'cr':
                            result_text += "\r"
        
        # 处理其他元素类型如需要
        elif tag_name in ['pPr', 'proofErr', 'bookmarkStart', 'bookmarkEnd']:
            # 跳过属性和其他非内容元素
            continue
    
    # 合并所有信息
    combined_info = []
    if formula_info:
        combined_info.extend(formula_info)
    if field_info:
        combined_info.extend(field_info)
    
    return result_text, combined_info if combined_info else None

def process_run_with_formulas(run, namespaces, formula_counter_start):
    """处理包含公式的文本运行"""
    result_text = ""
    formula_info = []
    formula_counter = formula_counter_start
    
    # 按顺序处理run的所有子元素
    for child in run:
        tag_name = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        
        if tag_name == 'oMath':  # 公式
            formula_placeholder = f"[formula_{formula_counter}]"
            result_text += formula_placeholder
            
            formula_info.append({
                'type': 'formula',
                'placeholder': formula_placeholder,
                'formula_number': formula_counter,
                'xml_content': etree.tostring(child, encoding='unicode'),
                'position': 'run_level'
            })
            formula_counter += 1
            
        elif tag_name == 't':  # 文本
            if child.text:
                result_text += child.text
        elif tag_name == 'br':
            result_text += "\n"
        elif tag_name == 'tab':
            result_text += "\t"
        elif tag_name == 'cr':
            result_text += "\r"
    
    return {
        'text': result_text,
        'formulas': formula_info
    }

def extract_smartart_content(docx, content_data, item_id, namespaces):
    """Extract text from SmartArt diagrams in Word document."""
    # Find all SmartArt diagram files
    diagram_drawings = [name for name in docx.namelist() 
                       if name.startswith('word/diagrams/drawing') and name.endswith('.xml')]
    diagram_drawings.sort()
    
    app_logger.info(f"Found {len(diagram_drawings)} SmartArt diagram files in Word document")
    
    for drawing_path in diagram_drawings:
        try:
            # Extract diagram number from path (e.g., drawing1.xml -> 1)
            diagram_match = re.search(r'drawing(\d+)\.xml', drawing_path)
            if not diagram_match:
                continue
            
            diagram_index = int(diagram_match.group(1))
            
            drawing_xml = docx.read(drawing_path)
            drawing_tree = etree.fromstring(drawing_xml)
            
            # Find all shapes with text content in SmartArt
            shapes = drawing_tree.xpath('.//dsp:sp[.//dsp:txBody]', namespaces=namespaces)
            
            for shape_index, shape in enumerate(shapes):
                model_id = shape.get('modelId', '')
                
                # Get text content from txBody elements
                tx_bodies = shape.xpath('.//dsp:txBody', namespaces=namespaces)
                
                for tx_body_index, tx_body in enumerate(tx_bodies):
                    paragraphs = tx_body.xpath('.//a:p', namespaces=namespaces)
                    
                    for p_index, paragraph in enumerate(paragraphs):
                        text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
                        
                        if not text_runs:
                            continue
                        
                        # Process runs and preserve exact spacing
                        run_info = process_smartart_text_runs(text_runs, namespaces)
                        
                        if not run_info['merged_text'].strip():
                            continue
                        
                        # Only process if there's meaningful text content and it should be translated
                        if should_translate_enhanced(run_info['merged_text']):
                            item_id += 1
                            content_data.append({
                                "id": item_id,
                                "count_src": item_id,
                                "diagram_index": diagram_index,
                                "shape_index": shape_index,
                                "tx_body_index": tx_body_index,
                                "paragraph_index": p_index,
                                "model_id": model_id,
                                "type": "smartart",
                                "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                                "run_texts": run_info['run_texts'],
                                "run_styles": run_info['run_styles'],
                                "run_lengths": run_info['run_lengths'],
                                "drawing_path": drawing_path,
                                "original_text": run_info['merged_text'],  # Store original text for data.xml matching
                                "xpath": f".//dsp:sp[{shape_index + 1}]//dsp:txBody[{tx_body_index + 1}]//a:p[{p_index + 1}]"
                            })
                        
        except Exception as e:
            app_logger.error(f"Failed to extract SmartArt from {drawing_path}: {e}")
            continue
    
    return item_id

def process_smartart_text_runs(text_runs, namespaces):
    """Process SmartArt text runs and preserve exact spacing and formatting."""
    merged_text = ""
    run_texts = []
    run_styles = []
    run_lengths = []
    
    for text_run in text_runs:
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if text_node and text_node[0].text is not None:
            run_text = text_node[0].text
        else:
            run_text = ""
        
        # Preserve exact text content including spaces
        merged_text += run_text
        run_texts.append(run_text)
        run_lengths.append(len(run_text))
        run_styles.append(extract_smartart_run_style(text_run, namespaces))
    
    return {
        'merged_text': merged_text,
        'run_texts': run_texts,
        'run_styles': run_styles,
        'run_lengths': run_lengths
    }

def extract_smartart_run_style(text_run, namespaces):
    """Extract comprehensive style information from a SmartArt text run."""
    style_info = {}
    
    try:
        rpr = text_run.xpath('./a:rPr', namespaces=namespaces)
        if rpr:
            rpr_element = rpr[0]
            
            # Font size
            sz = rpr_element.get('sz')
            if sz:
                style_info['font_size'] = sz
            
            # Bold
            b = rpr_element.get('b')
            if b:
                style_info['bold'] = b
            
            # Italic
            i = rpr_element.get('i')
            if i:
                style_info['italic'] = i
            
            # Underline
            u = rpr_element.get('u')
            if u:
                style_info['underline'] = u
            
            # Font family
            latin = rpr_element.xpath('./a:latin', namespaces=namespaces)
            if latin:
                style_info['font_family'] = latin[0].get('typeface')
            
            # Font color
            solid_fill = rpr_element.xpath('./a:solidFill/a:srgbClr', namespaces=namespaces)
            if solid_fill:
                style_info['color'] = solid_fill[0].get('val')
            
            # Strike through
            strike = rpr_element.get('strike')
            if strike:
                style_info['strike'] = strike
                
    except Exception as e:
        app_logger.warning(f"Failed to extract SmartArt style information: {e}")
    
    return style_info

def parse_styles_xml(styles_xml, namespaces):
    """Parse styles.xml to understand style definitions"""
    styles_info = {}
    
    if not styles_xml:
        return styles_info
    
    try:
        styles_tree = etree.fromstring(styles_xml)
        
        # Parse style definitions
        styles = styles_tree.xpath('//w:style', namespaces=namespaces)
        for style in styles:
            style_id = style.get(f'{{{namespaces["w"]}}}styleId')
            style_type = style.get(f'{{{namespaces["w"]}}}type')
            
            if style_id:
                styles_info[style_id] = {
                    'type': style_type,
                    'name': None,
                    'basedOn': None,
                    'next': None
                }
                
                # Get style name
                name_nodes = style.xpath('.//w:name', namespaces=namespaces)
                if name_nodes:
                    styles_info[style_id]['name'] = name_nodes[0].get(f'{{{namespaces["w"]}}}val')
                
                # Get basedOn
                basedOn_nodes = style.xpath('.//w:basedOn', namespaces=namespaces)
                if basedOn_nodes:
                    styles_info[style_id]['basedOn'] = basedOn_nodes[0].get(f'{{{namespaces["w"]}}}val')
                
                # Get next
                next_nodes = style.xpath('.//w:next', namespaces=namespaces)
                if next_nodes:
                    styles_info[style_id]['next'] = next_nodes[0].get(f'{{{namespaces["w"]}}}val')
        
    except Exception as e:
        app_logger.error(f"Error parsing styles.xml: {e}")
    
    return styles_info

def process_document_content(document_tree, content_data, item_id, numbering_info, styles_info, namespaces):
    """Process main document content with better structure handling and progress tracking - enhanced error recovery"""
    
    app_logger.info("Starting document content processing...")
    
    # First, process SDT (Structured Document Tags) content like TOC
    try:
        item_id = process_sdt_content(document_tree, content_data, item_id, numbering_info, styles_info, namespaces)
    except Exception as e:
        app_logger.error(f"Error processing SDT content: {e}")
        app_logger.error(f"Stack trace:", exc_info=True)
        # 继续处理其他内容
    
    # Get all body elements (including nested ones)
    try:
        body_elements = get_all_body_elements(document_tree, namespaces)
    except Exception as e:
        app_logger.error(f"Error getting body elements: {e}")
        app_logger.error(f"Stack trace:", exc_info=True)
        return item_id
    
    total_elements = len(body_elements)
    app_logger.info(f"Processing {total_elements} main document elements...")
    
    processed_count = 0
    last_progress_report = 0
    failed_elements = []
    
    for element_index, element in enumerate(body_elements):
        try:
            # 进度追踪
            processed_count += 1
            progress_percent = (processed_count * 100) // total_elements if total_elements > 0 else 100
            if progress_percent >= last_progress_report + 20:
                app_logger.info(f"Document processing progress: {progress_percent}% ({processed_count}/{total_elements})")
                last_progress_report = progress_percent
            
            element_type = element.tag.split('}')[-1] if '}' in element.tag else element.tag
            
            if element_type == 'p':
                try:
                    item_id = process_paragraph_element(
                        element, content_data, item_id, element_index, 
                        numbering_info, styles_info, namespaces
                    )
                except Exception as e:
                    app_logger.warning(f"Error processing paragraph at index {element_index}: {e}")
                    failed_elements.append(('paragraph', element_index))
                    continue
            
            elif element_type == 'tbl':
                try:
                    table_rows = element.xpath('./w:tr', namespaces=namespaces)
                    app_logger.debug(f"Processing table {element_index} with {len(table_rows)} rows")
                    
                    # 添加更详细的错误处理
                    try:
                        table_props = extract_table_properties(element, namespaces)
                    except Exception as e:
                        app_logger.warning(f"Error extracting table properties for table {element_index}: {e}")
                        table_props = {}
                    
                    try:
                        item_id = process_table_element(
                            element, content_data, item_id, element_index, 
                            numbering_info, styles_info, namespaces
                        )
                    except Exception as e:
                        app_logger.error(f"Error in process_table_element for table {element_index}: {e}")
                        app_logger.error(f"Stack trace:", exc_info=True)
                        raise
                    
                    app_logger.debug(f"Completed processing table {element_index}")
                    
                except Exception as e:
                    app_logger.error(f"Error processing table {element_index}: {e}")
                    failed_elements.append(('table', element_index))
                    # 继续处理下一个元素
                    continue
            
            elif element_type == 'sdt':
                # Skip SDT elements as they're processed separately
                continue
                
        except Exception as e:
            app_logger.error(f"Unexpected error processing element {element_index}: {e}")
            app_logger.error(f"Stack trace:", exc_info=True)
            failed_elements.append(('unknown', element_index))
            continue
    
    if failed_elements:
        app_logger.warning(f"Failed to process {len(failed_elements)} elements: {failed_elements}")
    
    app_logger.info(f"Document content processing completed. Processed {processed_count} elements successfully, failed {len(failed_elements)} elements.")
    
    # Process textboxes separately to avoid duplication
    app_logger.info("Processing textboxes...")
    try:
        textbox_items = extract_textbox_content(document_tree, namespaces)
        for textbox_item in textbox_items:
            item_id += 1
            textbox_item["id"] = item_id
            textbox_item["count_src"] = item_id
            content_data.append(textbox_item)
        
        app_logger.info(f"Textbox processing completed. Found {len(textbox_items)} textboxes.")
    except Exception as e:
        app_logger.error(f"Error processing textboxes: {e}")
        app_logger.error(f"Stack trace:", exc_info=True)
    
    return item_id

def process_sdt_content(document_tree, content_data, item_id, numbering_info, styles_info, namespaces):
    """Process Structured Document Tags (SDT) content, especially TOC"""
    
    # Find all SDT elements
    sdt_elements = document_tree.xpath('.//w:sdt', namespaces=namespaces)
    
    for sdt_index, sdt in enumerate(sdt_elements):
        # Check if this is a TOC SDT
        is_toc_sdt = False
        sdt_props = sdt.xpath('./w:sdtPr', namespaces=namespaces)
        
        if sdt_props:
            # Check for Table of Contents gallery
            doc_part_objs = sdt_props[0].xpath('.//w:docPartObj', namespaces=namespaces)
            for doc_part_obj in doc_part_objs:
                gallery = doc_part_obj.xpath('.//w:docPartGallery', namespaces=namespaces)
                if gallery and gallery[0].get(f'{{{namespaces["w"]}}}val') == 'Table of Contents':
                    is_toc_sdt = True
                    break
        
        # Process SDT content
        sdt_content = sdt.xpath('./w:sdtContent', namespaces=namespaces)
        if sdt_content:
            # Process paragraphs within SDT
            sdt_paragraphs = sdt_content[0].xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            for para_index, paragraph in enumerate(sdt_paragraphs):
                # Enhanced TOC detection for SDT content
                is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, is_toc_sdt)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
                    full_text = toc_title_text
                    field_info = None
                else:
                    # Extract paragraph text with formulas support
                    full_text, field_info = process_paragraph_element_with_formulas(paragraph, namespaces)
                
                if full_text and full_text.strip() and should_translate_enhanced(full_text):
                    item_id += 1
                    item_data = {
                        "id": item_id,
                        "count_src": item_id,
                        "type": "sdt_paragraph",
                        "sdt_index": sdt_index,
                        "paragraph_index": para_index,
                        "is_toc_sdt": is_toc_sdt,
                        "is_toc": is_toc,
                        "value": full_text.replace("\n", "␊").replace("\r", "␍"),
                        "original_pPr": extract_paragraph_properties(paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(paragraph, namespaces),
                        "sdt_props": extract_sdt_properties(sdt, namespaces)
                    }
                    
                    if field_info:
                        item_data["field_info"] = field_info
                    
                    if is_toc:
                        item_data.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    content_data.append(item_data)
                    app_logger.debug(f"Extracted SDT paragraph {item_id}: '{full_text[:50]}...'")
            
            # Process tables within SDT
            sdt_tables = sdt_content[0].xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            for table_index, table in enumerate(sdt_tables):
                table_props = extract_table_properties(table, namespaces)
                item_id = process_sdt_table_recursive(
                    table, content_data, item_id, sdt_index, table_index,
                    numbering_info, styles_info, namespaces, table_props, is_toc_sdt
                )
    
    return item_id

def process_sdt_table_recursive(table, content_data, item_id, sdt_index, table_index, numbering_info, styles_info, namespaces, table_props, is_toc_sdt, nesting_level=0):
    """Process tables within SDT recursively"""
    
    rows = table.xpath('./w:tr', namespaces=namespaces)
    
    for row_idx, row in enumerate(rows):
        row_props = extract_row_properties(row, namespaces)
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        for cell_idx, cell in enumerate(cells):
            cell_props = extract_cell_properties(cell, namespaces)
            
            # Process cell paragraphs
            cell_paragraphs = cell.xpath('./w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            for para_idx, cell_paragraph in enumerate(cell_paragraphs):
                # Enhanced TOC detection for paragraphs in SDT tables
                is_toc, toc_info = detect_toc_paragraph_enhanced(cell_paragraph, namespaces, is_toc_sdt)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(cell_paragraph, namespaces)
                    cell_text = toc_title_text
                    cell_field_info = None
                else:
                    # Fixed: Use formula-aware extraction
                    cell_text, cell_field_info = process_paragraph_element_with_formulas(cell_paragraph, namespaces)
                    toc_structure = None
                
                if cell_text and cell_text.strip() and should_translate_enhanced(cell_text):
                    item_id += 1
                    cell_data = {
                        "id": item_id,
                        "count_src": item_id,
                        "type": "sdt_table_cell",
                        "sdt_index": sdt_index,
                        "table_index": table_index,
                        "row": row_idx,
                        "col": cell_idx,
                        "paragraph_index": para_idx,
                        "nesting_level": nesting_level,
                        "is_toc_sdt": is_toc_sdt,
                        "is_toc": is_toc,
                        "value": cell_text.replace("\n", "␊").replace("\r", "␍"),
                        "table_props": table_props,
                        "row_props": row_props,
                        "cell_props": cell_props,
                        "original_pPr": extract_paragraph_properties(cell_paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(cell_paragraph, namespaces)
                    }
                    
                    if cell_field_info:
                        cell_data["field_info"] = cell_field_info
                    
                    if is_toc:
                        cell_data.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    content_data.append(cell_data)
                    app_logger.debug(f"Extracted SDT table cell {item_id}: '{cell_text[:50]}...'")
            
            # Process nested tables
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            for nested_table_idx, nested_table in enumerate(nested_tables):
                nested_table_props = extract_table_properties(nested_table, namespaces)
                item_id = process_sdt_table_recursive(
                    nested_table, content_data, item_id, sdt_index,
                    f"{table_index}_nested_{row_idx}_{cell_idx}_{nested_table_idx}",
                    numbering_info, styles_info, namespaces, 
                    nested_table_props, is_toc_sdt, nesting_level + 1
                )
    
    return item_id

def extract_sdt_properties(sdt, namespaces):
    """Extract SDT properties for format preservation"""
    sdt_props = {}
    
    sdt_pr = sdt.xpath('./w:sdtPr', namespaces=namespaces)
    if sdt_pr:
        sdt_props['sdtPr_xml'] = etree.tostring(sdt_pr[0], encoding='unicode')
    
    sdt_end_pr = sdt.xpath('./w:sdtEndPr', namespaces=namespaces)
    if sdt_end_pr:
        sdt_props['sdtEndPr_xml'] = etree.tostring(sdt_end_pr[0], encoding='unicode')
    
    return sdt_props

def detect_toc_paragraph_enhanced(paragraph, namespaces, is_in_toc_sdt=False):
    """Enhanced TOC detection that handles SDT and complex structures"""
    
    # If paragraph is in a TOC SDT, it's likely a TOC entry
    if is_in_toc_sdt:
        # Check for hyperlink structure typical of TOC entries
        hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
        if hyperlinks:
            hyperlink = hyperlinks[0]
            anchor = hyperlink.get(f'{{{namespaces["w"]}}}anchor', '')
            
            # Check for bookmark anchors
            if anchor and (anchor.startswith('bookmark') or anchor.startswith('_Toc') or anchor.startswith('_Ref')):
                # Check for typical TOC pattern with tabs and page numbers
                paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
                if has_toc_pattern_enhanced(paragraph_text) or has_tab_structure(paragraph, namespaces):
                    toc_info = {
                        'style': 'sdt_hyperlink_based',
                        'level': detect_toc_level_from_sdt_formatting(paragraph, namespaces),
                        'detection_method': 'sdt_hyperlink_pattern',
                        'anchor': anchor,
                        'in_sdt': True
                    }
                    return True, toc_info
        
        # Even without hyperlinks, if in TOC SDT and has tab structure, likely TOC
        if has_tab_structure(paragraph, namespaces):
            paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
            if paragraph_text.strip():  # Has actual content
                toc_info = {
                    'style': 'sdt_tab_based',
                    'level': detect_toc_level_from_sdt_formatting(paragraph, namespaces),
                    'detection_method': 'sdt_tab_structure',
                    'in_sdt': True
                }
                return True, toc_info
    
    # Fallback to original TOC detection
    return detect_toc_paragraph(paragraph, namespaces)

def has_toc_pattern_enhanced(text):
    """Enhanced pattern detection for TOC entries"""
    if not text or len(text.strip()) < 2:
        return False
    
    # Clean the text first - remove page numbers from the end for pattern matching
    text_clean = text.strip()
    
    # Remove page numbers from the end
    text_clean = re.sub(r'\s*\.\d+\s*$', '', text_clean)  # Remove .57, .123 etc
    text_clean = re.sub(r'\s*\d+\s*$', '', text_clean)    # Remove trailing numbers
    text_clean = re.sub(r'\.{3,}\s*$', '', text_clean)    # Remove trailing dots
    text_clean = text_clean.strip()
    
    if len(text_clean) < 2:
        return False
    
    # Enhanced TOC patterns including Spanish and other languages
    # 注意：这些模式都要求文本**以数字结尾**或包含典型的TOC格式（点引导线等）
    patterns = [
        r'.+\.{3,}\s*\d+$',          # Text...123 (有点引导线+数字)
        r'.+\t+\d+$',                # Text    123 (tab+数字结尾)
        r'.+\s{5,}\d+$',             # Text     123 (多个空格+数字)
        r'.+\.\s*\.+\s*\d+$',        # Text. ... 123
        r'^\d+\.?\d*\s+.+\s+\d+$',   # 1.1 Text 123 (开头数字+中间文本+结尾数字)
        r'^[A-Z][A-ZÁÉÍÓÚÜÑ\s]+\s+\d+$',  # UPPERCASE TEXT 123
        r'.+\s*\.\d+$',              # Text .57 (点+数字结尾)
    ]
    
    # Test against original text
    for pattern in patterns:
        if re.search(pattern, text.strip(), re.IGNORECASE):
            return True
    
    # 【关键修复】不要仅仅因为以数字开头或有字母就认为是TOC
    # 移除了原来过于宽松的判断逻辑
    
    return False

def has_tab_structure(paragraph, namespaces):
    """Check if paragraph has tab structure typical of TOC"""
    tabs = paragraph.xpath('.//w:tab', namespaces=namespaces)
    return len(tabs) > 0

def detect_toc_level_from_sdt_formatting(paragraph, namespaces):
    """Detect TOC level from SDT paragraph formatting"""
    # Check indentation
    ind_elements = paragraph.xpath('.//w:ind', namespaces=namespaces)
    if ind_elements:
        left_indent = ind_elements[0].get(f'{{{namespaces["w"]}}}left', '0')
        try:
            indent_value = safe_convert_to_int(left_indent)
            # Estimate level based on indentation
            level = max(1, (indent_value // 400) + 1)  # Adjusted for typical SDT indentation
            return min(level, 9)
        except:
            pass
    
    # Check style-based level
    style_elements = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    if style_elements:
        style_val = style_elements[0].get(f'{{{namespaces["w"]}}}val', '').lower()
        level_match = re.search(r'(\d+)', style_val)
        if level_match:
            return safe_convert_to_int(level_match.group(1))
    
    return 1

def extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces):
    """Enhanced TOC title extraction that handles complex SDT structures"""
    
    # First try the original method
    title_text, structure = extract_toc_title_with_complete_structure(paragraph, namespaces)
    
    # Enhanced processing for SDT TOC entries
    if not title_text or len(title_text.strip()) < 2:
        # Try alternative extraction for complex structures
        title_text, structure = extract_toc_title_alternative(paragraph, namespaces)
    
    # Clean brackets from the extracted title text
    title_text = clean_translation_brackets(title_text)
    
    return title_text, structure

def extract_toc_title_alternative(paragraph, namespaces):
    """Alternative method for extracting TOC title from complex structures"""
    
    # Get all text content first
    all_text_nodes = paragraph.xpath('.//w:t', namespaces=namespaces)
    all_run_texts = []
    
    # Collect text from each run separately to maintain structure
    all_runs = paragraph.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    
    for run in all_runs:
        text_nodes = run.xpath('.//w:t', namespaces=namespaces)
        run_text = ''.join(node.text or '' for node in text_nodes)
        all_run_texts.append(run_text)
    
    # Join all text to get the full content
    full_text = ''.join(all_run_texts)
    
    # Identify title vs page number and other elements
    title_parts = []
    
    for i, run_text in enumerate(all_run_texts):
        if not run_text.strip():
            # Empty or whitespace - add to title to maintain spacing
            if title_parts or i < len(all_run_texts) - 3:  # Don't add trailing spaces
                title_parts.append(run_text)
            continue
        
        # Check if this is a page number
        if is_likely_page_number(run_text):
            # This is a page number, stop adding to title
            break
        
        # Check if this is dot leaders
        if is_dot_leader(run_text):
            # This is dot leaders, stop adding to title
            break
        
        # Check if this is a tab
        run = all_runs[i] if i < len(all_runs) else None
        if run is not None and run.xpath('.//w:tab', namespaces=namespaces):
            # This is a tab, stop adding to title
            break
        
        # Add to title
        title_parts.append(run_text)
    
    # Join title parts and clean up
    title_text = ''.join(title_parts).strip()
    
    # Remove trailing dots that might be part of leaders
    title_text = re.sub(r'\.+$', '', title_text).strip()
    
    # Remove any remaining page number patterns from the end
    title_text = re.sub(r'\s*\.\d+\s*$', '', title_text).strip()
    title_text = re.sub(r'\s*\d+\s*$', '', title_text).strip()
    
    # Clean brackets from the extracted title text
    title_text = clean_translation_brackets(title_text)
    
    # Create simplified structure
    structure = {
        'total_runs': len(all_runs),
        'title_runs': [],
        'tab_runs': [],
        'leader_runs': [],
        'page_number_runs': [],
        'field_runs': [],
        'hyperlink_info': None,
        'run_details': [],
        'extraction_method': 'alternative'
    }
    
    app_logger.debug(f"Extracted TOC title (alternative): '{title_text}'")
    
    return title_text, structure

_element_cache = {}

def get_all_body_elements(document_tree, namespaces):
    """Get all body elements including those in nested structures - fixed caching"""
    # 使用元素的内存地址作为缓存键
    cache_key = id(document_tree)
    
    # 检查缓存
    if cache_key in _element_cache:
        return _element_cache[cache_key]
    
    # Get direct body children first
    body = document_tree.xpath('.//w:body', namespaces=namespaces)
    if not body:
        return []
    
    # 使用更高效的XPath查询，一次性获取所有需要的元素
    elements = body[0].xpath('./*[self::w:p or self::w:tbl][not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:txbxContent) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
    
    # 缓存结果
    _element_cache[cache_key] = elements
    app_logger.debug(f"Found {len(elements)} main body elements")
    
    return elements

def process_paragraph_element(paragraph, content_data, item_id, element_index, numbering_info, styles_info, namespaces):
    """Process a single paragraph element"""
    
    # Enhanced heading detection
    heading_styles = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    is_heading = False
    heading_level = None
    
    if heading_styles:
        style_val = heading_styles[0].get(f'{{{namespaces["w"]}}}val', '')
        if any(pattern in style_val.lower() for pattern in ['heading', 'title', 'caption', 'subtitle']):
            is_heading = True
            level_match = re.search(r'(\d+)', style_val)
            if level_match:
                heading_level = safe_convert_to_int(level_match.group(1))
    
    # Check for numbering
    numbering_props = paragraph.xpath('.//w:numPr', namespaces=namespaces)
    has_numbering = bool(numbering_props)
    paragraph_numbering_info = None
    
    if has_numbering:
        paragraph_numbering_info = extract_paragraph_numbering_info(
            numbering_props[0], numbering_info, namespaces)
    
    # Enhanced TOC detection
    is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, False)
    
    # Extract text including formulas and excluding textbox content but including page variables
    if is_toc:
        toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
        full_text = toc_title_text
        field_info = None
    else:
        full_text, field_info = process_paragraph_element_with_formulas(paragraph, namespaces)
        toc_structure = None
    
    if full_text and full_text.strip() and should_translate_enhanced(full_text):
        item_id += 1
        item_data = {
            "id": item_id,
            "count_src": item_id,
            "type": "paragraph",
            "is_heading": is_heading,
            "heading_level": heading_level,
            "has_numbering": has_numbering,
            "numbering_info": paragraph_numbering_info,
            "element_index": element_index,
            "style_info": get_comprehensive_paragraph_style_info(paragraph, namespaces),
            "value": full_text.replace("\n", "␊").replace("\r", "␍"),
            "original_pPr": extract_paragraph_properties(paragraph, namespaces),
            "original_structure": extract_paragraph_structure(paragraph, namespaces)
        }
        
        if field_info:
            item_data["field_info"] = field_info
        
        if is_toc:
            item_data.update({
                "is_toc": True,
                "toc_info": toc_info,
                "toc_structure": toc_structure
            })
        
        content_data.append(item_data)
        app_logger.debug(f"Extracted paragraph {item_id}: '{full_text[:50]}...'")
    
    return item_id

def process_table_element(table, content_data, item_id, element_index, numbering_info, styles_info, namespaces):
    """Process a table element with support for nested tables"""
    
    # Get table properties for format preservation
    table_props = extract_table_properties(table, namespaces)
    
    # Process all rows and cells, including nested tables
    item_id = process_table_rows_recursive(
        table, content_data, item_id, element_index, 
        numbering_info, styles_info, namespaces, table_props
    )
    
    return item_id

def process_table_rows_recursive(table, content_data, item_id, table_index, numbering_info, styles_info, namespaces, table_props, nesting_level=0):
    """Recursively process table rows and handle nested tables - enhanced error handling and nesting control"""
    
    # 严格限制嵌套深度，防止过深嵌套导致问题
    MAX_NESTING_LEVEL = 10  # 增加到10层以支持更深的嵌套
    if nesting_level >= MAX_NESTING_LEVEL:
        app_logger.warning(f"Reached maximum nesting level ({MAX_NESTING_LEVEL}) for table {table_index}, skipping deeper nested tables")
        return item_id
    
    try:
        # 获取表格信息用于日志
        rows = table.xpath('./w:tr', namespaces=namespaces)
        total_rows = len(rows)
        
        if nesting_level == 0:  # 只在顶级表格记录日志
            app_logger.info(f"Processing table {table_index} with {total_rows} rows at nesting level {nesting_level}")
        else:
            app_logger.debug(f"Processing nested table {table_index} with {total_rows} rows at nesting level {nesting_level}")
        
        # 防止处理过大的表格导致性能问题
        if total_rows > 1000:
            app_logger.warning(f"Large table detected ({total_rows} rows). Processing may take some time...")
        
        # 添加行处理计数器
        processed_rows = 0
        failed_rows = []
        
        for row_idx, row in enumerate(rows):
            try:
                # 进度追踪（仅对大表格）
                if total_rows > 50 and nesting_level == 0:
                    processed_rows += 1
                    if processed_rows % 50 == 0:
                        app_logger.debug(f"Table {table_index} processing progress: {processed_rows}/{total_rows} rows")
                
                # Get row properties
                try:
                    row_props = extract_row_properties(row, namespaces)
                except Exception as e:
                    app_logger.warning(f"Error extracting row properties for table {table_index} row {row_idx}: {e}")
                    row_props = {}
                
                # 使用更高效的XPath查询
                cells = row.xpath('./w:tc', namespaces=namespaces)
                
                for cell_idx, cell in enumerate(cells):
                    try:
                        # Get cell properties
                        try:
                            cell_props = extract_cell_properties(cell, namespaces)
                        except Exception as e:
                            app_logger.warning(f"Error extracting cell properties for table {table_index}[{row_idx}][{cell_idx}]: {e}")
                            cell_props = {}
                        
                        # Process cell content (paragraphs) - 优化查询
                        cell_paragraphs = cell.xpath('./w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
                        
                        for para_idx, cell_paragraph in enumerate(cell_paragraphs):
                            try:
                                # Enhanced TOC detection for table cells
                                is_toc, toc_info = detect_toc_paragraph_enhanced(cell_paragraph, namespaces, False)
                                
                                if is_toc:
                                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(cell_paragraph, namespaces)
                                    cell_text = toc_title_text
                                    cell_field_info = None
                                else:
                                    # Use formula-aware extraction
                                    cell_text, cell_field_info = process_paragraph_element_with_formulas(cell_paragraph, namespaces)
                                    toc_structure = None
                                
                                if cell_text and cell_text.strip() and should_translate_enhanced(cell_text):
                                    item_id += 1
                                    cell_data = {
                                        "id": item_id,
                                        "count_src": item_id,
                                        "type": "table_cell",
                                        "table_index": table_index,
                                        "row": row_idx,
                                        "col": cell_idx,
                                        "paragraph_index": para_idx,
                                        "nesting_level": nesting_level,
                                        "is_toc": is_toc,
                                        "value": cell_text.replace("\n", "␊").replace("\r", "␍"),
                                        "table_props": table_props,
                                        "row_props": row_props,
                                        "cell_props": cell_props,
                                        "original_pPr": extract_paragraph_properties(cell_paragraph, namespaces),
                                        "original_structure": extract_paragraph_structure(cell_paragraph, namespaces)
                                    }
                                    
                                    if cell_field_info:
                                        cell_data["field_info"] = cell_field_info
                                    
                                    if is_toc:
                                        cell_data.update({
                                            "toc_info": toc_info,
                                            "toc_structure": toc_structure
                                        })
                                    
                                    content_data.append(cell_data)
                                    
                                    # 减少详细日志以提高性能
                                    if item_id % 500 == 0:
                                        app_logger.debug(f"Processed {item_id} items, current: table cell '{cell_text[:30]}...'")
                                        
                            except Exception as e:
                                app_logger.warning(f"Error processing cell paragraph {para_idx} in table {table_index}[{row_idx}][{cell_idx}]: {e}")
                                continue
                        
                        # Check for nested tables in this cell - 优化查询
                        try:
                            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
                            if nested_tables:
                                app_logger.debug(f"Found {len(nested_tables)} nested tables in cell {table_index}[{row_idx}][{cell_idx}] at nesting level {nesting_level}")
                                
                            for nested_table_idx, nested_table in enumerate(nested_tables):
                                try:
                                    if nesting_level < MAX_NESTING_LEVEL - 1:
                                        # 构建嵌套表格的标识符
                                        nested_table_id = f"{table_index}_nested_{row_idx}_{cell_idx}_{nested_table_idx}"
                                        app_logger.debug(f"Processing nested table: {nested_table_id} at nesting level {nesting_level + 1}")
                                        
                                        try:
                                            nested_table_props = extract_table_properties(nested_table, namespaces)
                                        except Exception as e:
                                            app_logger.warning(f"Error extracting properties for nested table {nested_table_id}: {e}")
                                            nested_table_props = {}
                                        
                                        # 递归处理嵌套表格
                                        try:
                                            item_id = process_table_rows_recursive(
                                                nested_table, content_data, item_id, 
                                                nested_table_id,
                                                numbering_info, styles_info, namespaces, 
                                                nested_table_props, nesting_level + 1
                                            )
                                        except Exception as e:
                                            app_logger.error(f"Error in recursive processing of nested table {nested_table_id}: {e}")
                                            # 记录错误但继续处理
                                            continue
                                    else:
                                        app_logger.warning(f"Skipping nested table at depth {nesting_level + 1} in cell {table_index}[{row_idx}][{cell_idx}] to prevent excessive nesting")
                                except Exception as e:
                                    app_logger.error(f"Error processing nested table {nested_table_idx} in cell {table_index}[{row_idx}][{cell_idx}]: {e}")
                                    # 继续处理其他嵌套表格
                                    continue
                                    
                        except Exception as e:
                            app_logger.warning(f"Error checking for nested tables in cell {table_index}[{row_idx}][{cell_idx}]: {e}")
                            
                    except Exception as e:
                        app_logger.warning(f"Error processing cell {table_index}[{row_idx}][{cell_idx}]: {e}")
                        # 继续处理下一个单元格
                        continue
                        
            except Exception as e:
                app_logger.warning(f"Error processing row {row_idx} in table {table_index}: {e}")
                failed_rows.append(row_idx)
                # 继续处理下一行
                continue
        
        if failed_rows:
            app_logger.warning(f"Failed to process {len(failed_rows)} rows in table {table_index}: {failed_rows}")
        
        if nesting_level == 0:
            app_logger.info(f"Completed processing table {table_index} with {total_rows} rows (failed: {len(failed_rows)})")
        else:
            app_logger.debug(f"Completed processing nested table {table_index}")
            
    except Exception as e:
        app_logger.error(f"Critical error processing table {table_index} at nesting level {nesting_level}: {e}")
        app_logger.error(f"Stack trace:", exc_info=True)
        # 不抛出异常，返回当前的item_id以便继续处理
        
    return item_id

def extract_table_properties(table, namespaces):
    """Extract table properties for format preservation"""
    table_props = {}
    
    # Get table properties element
    tblPr = table.xpath('./w:tblPr', namespaces=namespaces)
    if tblPr:
        table_props['tblPr_xml'] = etree.tostring(tblPr[0], encoding='unicode')
    
    # Get table grid
    tblGrid = table.xpath('./w:tblGrid', namespaces=namespaces)
    if tblGrid:
        table_props['tblGrid_xml'] = etree.tostring(tblGrid[0], encoding='unicode')
    
    return table_props

def extract_row_properties(row, namespaces):
    """Extract row properties for format preservation"""
    row_props = {}
    
    trPr = row.xpath('./w:trPr', namespaces=namespaces)
    if trPr:
        row_props['trPr_xml'] = etree.tostring(trPr[0], encoding='unicode')
    
    return row_props

def extract_cell_properties(cell, namespaces):
    """Extract cell properties for format preservation"""
    cell_props = {}
    
    tcPr = cell.xpath('./w:tcPr', namespaces=namespaces)
    if tcPr:
        cell_props['tcPr_xml'] = etree.tostring(tcPr[0], encoding='unicode')
    
    return cell_props

def extract_paragraph_properties(paragraph, namespaces):
    """Extract paragraph properties for exact format preservation"""
    pPr = paragraph.xpath('./w:pPr', namespaces=namespaces)
    if pPr:
        return etree.tostring(pPr[0], encoding='unicode')
    return None

def extract_paragraph_structure(paragraph, namespaces):
    """Extract complete paragraph structure information - optimized version"""
    structure = {
        'total_runs': 0,
        'runs_info': [],
        'has_fields': False,
        'has_drawings': False,
        'has_formulas': False
    }
    
    # 使用更高效的XPath查询，一次性获取所有runs
    runs = paragraph.xpath('./w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    structure['total_runs'] = len(runs)
    
    # Check for formulas at paragraph level
    paragraph_formulas = paragraph.xpath('./m:oMath', namespaces=namespaces)
    if paragraph_formulas:
        structure['has_formulas'] = True
    
    # 预编译XPath表达式以提高性能
    text_xpath = etree.XPath('.//w:t', namespaces=namespaces)
    field_xpath = etree.XPath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces)
    drawing_xpath = etree.XPath('.//w:drawing | .//w:pict | .//mc:AlternateContent', namespaces=namespaces)
    formula_xpath = etree.XPath('.//m:oMath', namespaces=namespaces)
    break_xpath = etree.XPath('.//w:br | .//w:cr | .//w:tab', namespaces=namespaces)
    rpr_xpath = etree.XPath('./w:rPr', namespaces=namespaces)
    
    for run_idx, run in enumerate(runs):
        run_info = {
            'index': run_idx,
            'has_text': bool(text_xpath(run)),
            'has_fields': bool(field_xpath(run)),
            'has_drawings': bool(drawing_xpath(run)),
            'has_formulas': bool(formula_xpath(run)),
            'has_breaks': bool(break_xpath(run)),
            'rPr_xml': None
        }
        
        # Extract run properties
        rPr = rpr_xpath(run)
        if rPr:
            run_info['rPr_xml'] = etree.tostring(rPr[0], encoding='unicode')
        
        structure['runs_info'].append(run_info)
        
        if run_info['has_fields']:
            structure['has_fields'] = True
        if run_info['has_drawings']:
            structure['has_drawings'] = True
        if run_info['has_formulas']:
            structure['has_formulas'] = True
    
    return structure

def get_comprehensive_paragraph_style_info(paragraph, namespaces):
    """Extract comprehensive style information from paragraph"""
    style_info = {}
    
    # Get paragraph style
    pStyle_nodes = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    if pStyle_nodes:
        style_info['paragraph_style'] = pStyle_nodes[0].get(f'{{{namespaces["w"]}}}val', '')
    
    # Get all paragraph properties
    pPr = paragraph.xpath('./w:pPr', namespaces=namespaces)
    if pPr:
        # Get justification
        jc_nodes = pPr[0].xpath('.//w:jc', namespaces=namespaces)
        if jc_nodes:
            style_info['justification'] = jc_nodes[0].get(f'{{{namespaces["w"]}}}val', '')
        
        # Get indentation
        ind_nodes = pPr[0].xpath('.//w:ind', namespaces=namespaces)
        if ind_nodes:
            style_info['indentation'] = {}
            for attr in ['left', 'right', 'firstLine', 'hanging']:
                val = ind_nodes[0].get(f'{{{namespaces["w"]}}}{attr}')
                if val:
                    style_info['indentation'][attr] = val
        
        # Get spacing
        spacing_nodes = pPr[0].xpath('.//w:spacing', namespaces=namespaces)
        if spacing_nodes:
            style_info['spacing'] = {}
            for attr in ['before', 'after', 'line', 'lineRule']:
                val = spacing_nodes[0].get(f'{{{namespaces["w"]}}}{attr}')
                if val:
                    style_info['spacing'][attr] = val
    
    return style_info

def process_header_footer_content(hf_tree, content_data, item_id, numbering_info, styles_info, namespaces, hf_type, hf_file, hf_number):
    """Process header/footer content"""
    
    # Process SDT content in header/footer first
    item_id = process_sdt_content(hf_tree, content_data, item_id, numbering_info, styles_info, namespaces)
    
    # Process paragraphs in header/footer
    hf_paragraphs = hf_tree.xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
    for p_idx, paragraph in enumerate(hf_paragraphs):
        numbering_props = paragraph.xpath('.//w:numPr', namespaces=namespaces)
        paragraph_numbering_info = None
        if numbering_props:
            paragraph_numbering_info = extract_paragraph_numbering_info(
                numbering_props[0], numbering_info, namespaces)
        
        # Enhanced TOC detection for header/footer
        is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, False)
        
        if is_toc:
            toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
            paragraph_text = toc_title_text
            field_info = None
        else:
            paragraph_text, field_info = process_paragraph_element_with_formulas(paragraph, namespaces)
            toc_structure = None
        
        if paragraph_text and paragraph_text.strip() and should_translate_enhanced(paragraph_text):
            item_id += 1
            item_data = {
                "id": item_id,
                "count_src": item_id,
                "type": "header_footer",
                "hf_type": hf_type,
                "hf_file": hf_file,
                "hf_number": hf_number,
                "paragraph_index": p_idx,
                "has_numbering": bool(numbering_props),
                "numbering_info": paragraph_numbering_info,
                "is_toc": is_toc,
                "value": paragraph_text.replace("\n", "␊").replace("\r", "␍"),
                "original_pPr": extract_paragraph_properties(paragraph, namespaces),
                "original_structure": extract_paragraph_structure(paragraph, namespaces)
            }
            
            if field_info:
                item_data["field_info"] = field_info
            
            if is_toc:
                item_data.update({
                    "toc_info": toc_info,
                    "toc_structure": toc_structure
                })
            
            content_data.append(item_data)
    
    # Process textboxes in header/footer
    hf_textbox_items = extract_textbox_content(hf_tree, namespaces)
    for textbox_item in hf_textbox_items:
        item_id += 1
        textbox_item["id"] = item_id
        textbox_item["count_src"] = item_id
        textbox_item["type"] = "header_footer_textbox"
        textbox_item["hf_type"] = hf_type
        textbox_item["hf_file"] = hf_file
        textbox_item["hf_number"] = hf_number
        content_data.append(textbox_item)
    
    # Process tables in header/footer (including nested tables)
    hf_tables = hf_tree.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
    for tbl_idx, table in enumerate(hf_tables):
        table_props = extract_table_properties(table, namespaces)
        item_id = process_header_footer_table_recursive(
            table, content_data, item_id, tbl_idx, numbering_info, styles_info, 
            namespaces, hf_type, hf_file, hf_number, table_props
        )
    
    return item_id

def process_header_footer_table_recursive(table, content_data, item_id, table_index, numbering_info, styles_info, namespaces, hf_type, hf_file, hf_number, table_props, nesting_level=0):
    """Process header/footer tables recursively"""
    
    rows = table.xpath('./w:tr', namespaces=namespaces)
    
    for row_idx, row in enumerate(rows):
        row_props = extract_row_properties(row, namespaces)
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        for cell_idx, cell in enumerate(cells):
            cell_props = extract_cell_properties(cell, namespaces)
            
            # Process cell paragraphs
            cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
            for para_idx, cell_paragraph in enumerate(cell_paragraphs):
                # Enhanced TOC detection for header/footer table cells
                is_toc, toc_info = detect_toc_paragraph_enhanced(cell_paragraph, namespaces, False)
                
                if is_toc:
                    toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(cell_paragraph, namespaces)
                    cell_text = toc_title_text
                    cell_field_info = None
                else:
                    # Use formula-aware extraction
                    cell_text, cell_field_info = process_paragraph_element_with_formulas(cell_paragraph, namespaces)
                    toc_structure = None
                
                if cell_text and cell_text.strip() and should_translate_enhanced(cell_text):
                    item_id += 1
                    cell_data = {
                        "id": item_id,
                        "count_src": item_id,
                        "type": "header_footer_table_cell",
                        "hf_type": hf_type,
                        "hf_file": hf_file,
                        "hf_number": hf_number,
                        "table_index": table_index,
                        "row": row_idx,
                        "col": cell_idx,
                        "paragraph_index": para_idx,
                        "nesting_level": nesting_level,
                        "is_toc": is_toc,
                        "value": cell_text.replace("\n", "␊").replace("\r", "␍"),
                        "table_props": table_props,
                        "row_props": row_props,
                        "cell_props": cell_props,
                        "original_pPr": extract_paragraph_properties(cell_paragraph, namespaces),
                        "original_structure": extract_paragraph_structure(cell_paragraph, namespaces)
                    }
                    
                    if cell_field_info:
                        cell_data["field_info"] = cell_field_info
                    
                    if is_toc:
                        cell_data.update({
                            "toc_info": toc_info,
                            "toc_structure": toc_structure
                        })
                    
                    content_data.append(cell_data)
            
            # Process nested tables
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            for nested_table_idx, nested_table in enumerate(nested_tables):
                nested_table_props = extract_table_properties(nested_table, namespaces)
                item_id = process_header_footer_table_recursive(
                    nested_table, content_data, item_id, 
                    f"{table_index}_nested_{row_idx}_{cell_idx}_{nested_table_idx}",
                    numbering_info, styles_info, namespaces, 
                    hf_type, hf_file, hf_number, nested_table_props, nesting_level + 1
                )
    
    return item_id

def detect_toc_paragraph(paragraph, namespaces):
    """Enhanced TOC detection that identifies various TOC styles and formats"""
    # Check for TOC styles
    toc_styles = paragraph.xpath('.//w:pStyle', namespaces=namespaces)
    if toc_styles:
        style_val = toc_styles[0].get(f'{{{namespaces["w"]}}}val', '').lower()
        
        # Common TOC style patterns
        toc_patterns = [
            'toc',           # Standard TOC styles
            'tableofcontents',
            'contents',
            'outline',
            'index'
        ]
        
        if any(pattern in style_val for pattern in toc_patterns):
            toc_info = {
                'style': style_val,
                'level': extract_toc_level_from_style(style_val),
                'detection_method': 'style'
            }
            return True, toc_info
    
    # Check for TOC field codes
    toc_fields = paragraph.xpath('.//w:instrText[contains(text(), "TOC")]', namespaces=namespaces)
    if toc_fields:
        toc_info = {
            'style': 'field_based',
            'level': None,
            'detection_method': 'field',
            'field_instruction': toc_fields[0].text if toc_fields[0].text else ''
        }
        return True, toc_info
    
    # Check for hyperlink-based TOC (common in generated TOCs)
    hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
    if hyperlinks:
        # Look for patterns that suggest this is a TOC entry
        hyperlink = hyperlinks[0]
        anchor = hyperlink.get(f'{{{namespaces["w"]}}}anchor', '')
        
        # TOC hyperlinks often have anchor patterns like _Toc123456 or similar
        if anchor and (anchor.startswith('_Toc') or anchor.startswith('_Ref')):
            # Check if the paragraph contains typical TOC elements (dots, page numbers)
            paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
            if has_toc_pattern_enhanced(paragraph_text):
                toc_info = {
                    'style': 'hyperlink_based',
                    'level': detect_toc_level_from_formatting(paragraph, namespaces),
                    'detection_method': 'hyperlink_pattern',
                    'anchor': anchor
                }
                return True, toc_info
    
    # Check for tab and dot leader patterns typical of TOC
    # 【关键修复】只有当文本真正符合TOC格式时才判断为TOC
    paragraph_text = extract_paragraph_text_only(paragraph, namespaces)
    if has_toc_pattern_enhanced(paragraph_text):
        # 确认有tab字符（TOC常见特征）
        tabs = paragraph.xpath('.//w:tab', namespaces=namespaces)
        if tabs:
            toc_info = {
                'style': 'pattern_based',
                'level': detect_toc_level_from_formatting(paragraph, namespaces),
                'detection_method': 'pattern_analysis'
            }
            return True, toc_info
    
    return False, None

def extract_toc_level_from_style(style_val):
    """Extract TOC level from style name"""
    # Look for numbers in style name (e.g., TOC1, TOC2, etc.)
    level_match = re.search(r'(\d+)', style_val)
    return safe_convert_to_int(level_match.group(1)) if level_match else 1

def detect_toc_level_from_formatting(paragraph, namespaces):
    """Detect TOC level from paragraph formatting like indentation"""
    ind_elements = paragraph.xpath('.//w:ind', namespaces=namespaces)
    if ind_elements:
        left_indent = ind_elements[0].get(f'{{{namespaces["w"]}}}left', '0')
        try:
            indent_value = safe_convert_to_int(left_indent)
            # Estimate level based on indentation (assuming 720 twips per level)
            level = max(1, (indent_value // 720) + 1)
            return min(level, 9)  # Cap at level 9
        except:
            return 1
    return 1

def extract_paragraph_text_only(paragraph, namespaces):
    """Extract only the text content without processing fields or variables"""
    text_nodes = paragraph.xpath('.//w:t', namespaces=namespaces)
    full_text = ''.join(node.text or '' for node in text_nodes)
    
    # Clean brackets from the extracted text
    return clean_translation_brackets(full_text)

def extract_toc_title_with_complete_structure(paragraph, namespaces):
    """Extract only the title text from TOC entry, preserving complete structure for accurate restoration"""
    all_runs = paragraph.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    
    structure = {
        'total_runs': len(all_runs),
        'title_runs': [],           # Indices of runs containing title text
        'tab_runs': [],             # Indices of runs containing tabs
        'leader_runs': [],          # Indices of runs containing dot leaders
        'page_number_runs': [],     # Indices of runs containing page numbers
        'field_runs': [],           # Indices of runs containing fields
        'hyperlink_info': None,     # Hyperlink information
        'run_details': []           # Detailed information about each run
    }
    
    title_text = ""
    
    # Check for hyperlink structure
    hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
    if hyperlinks:
        hyperlink = hyperlinks[0]
        structure['hyperlink_info'] = {
            'anchor': hyperlink.get(f'{{{namespaces["w"]}}}anchor', ''),
            'tooltip': hyperlink.get(f'{{{namespaces["w"]}}}tooltip', ''),
            'start_run_index': None,
            'end_run_index': None
        }
        
        # Find which runs are inside the hyperlink
        hyperlink_runs = hyperlink.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        for run in hyperlink_runs:
            if run in all_runs:
                run_index = all_runs.index(run)
                if structure['hyperlink_info']['start_run_index'] is None:
                    structure['hyperlink_info']['start_run_index'] = run_index
                structure['hyperlink_info']['end_run_index'] = run_index
    
    # First pass: identify all runs and their content
    run_contents = []
    for run_idx, run in enumerate(all_runs):
        run_detail = {
            'index': run_idx,
            'type': 'text',           # text, tab, leader, page_number, field
            'content': '',
            'is_in_hyperlink': False,
            'formatting': None
        }
        
        # Check if run is in hyperlink
        if structure['hyperlink_info']:
            start_idx = structure['hyperlink_info']['start_run_index']
            end_idx = structure['hyperlink_info']['end_run_index']
            if start_idx is not None and end_idx is not None:
                if start_idx <= run_idx <= end_idx:
                    run_detail['is_in_hyperlink'] = True
        
        # Get run formatting
        rPr_elements = run.xpath('./w:rPr', namespaces=namespaces)
        if rPr_elements:
            run_detail['formatting'] = etree.tostring(rPr_elements[0], encoding='unicode')
        
        # Check for tabs
        if run.xpath('.//w:tab', namespaces=namespaces):
            run_detail['type'] = 'tab'
            structure['tab_runs'].append(run_idx)
        
        # Check for field codes
        elif run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces):
            run_detail['type'] = 'field'
            structure['field_runs'].append(run_idx)
            
            # Extract field content for analysis
            field_content = ""
            for child in run:
                if child.tag.endswith('fldSimple'):
                    field_content = child.get(f'{{{namespaces["w"]}}}instr', '')
                elif child.tag.endswith('instrText'):
                    field_content = child.text or ''
            run_detail['content'] = field_content
        
        # Check for regular text
        else:
            text_content = ""
            for child in run:
                if child.tag.endswith('t'):
                    text_content += child.text or ""
            
            run_detail['content'] = text_content
            run_contents.append((run_idx, text_content))
        
        structure['run_details'].append(run_detail)
    
    # Second pass: analyze text runs with context
    # Look for page numbers from the end, as they typically appear at the end
    for i in range(len(run_contents) - 1, -1, -1):
        run_idx, text_content = run_contents[i]
        run_detail = structure['run_details'][run_idx]
        
        if text_content.strip():
            # Check if this looks like a page number
            if is_likely_page_number(text_content):
                run_detail['type'] = 'page_number'
                structure['page_number_runs'].append(run_idx)
            elif is_dot_leader(text_content):
                run_detail['type'] = 'leader'
                structure['leader_runs'].append(run_idx)
            else:
                # Check if this is isolated punctuation or numbering that shouldn't be translated
                if is_isolated_punctuation_or_numbering(text_content):
                    run_detail['type'] = 'leader'  # Treat as leader to exclude from title
                    structure['leader_runs'].append(run_idx)
                else:
                    # This is likely title text
                    run_detail['type'] = 'title'
                    structure['title_runs'].append(run_idx)
    
    # Third pass: extract title text in order
    title_runs_sorted = sorted([idx for idx in structure['title_runs']])
    for run_idx in title_runs_sorted:
        run_detail = structure['run_details'][run_idx]
        title_text += run_detail['content']
    
    # Clean up title text
    title_text = title_text.strip()
    title_text = re.sub(r'\s+', ' ', title_text)
    
    # Clean brackets from the extracted title text
    title_text = clean_translation_brackets(title_text)
    
    app_logger.debug(f"Extracted TOC title: '{title_text}', Structure: {len(structure['title_runs'])} title runs, "
                    f"{len(structure['tab_runs'])} tab runs, {len(structure['page_number_runs'])} page number runs")
    
    return title_text, structure

def is_isolated_punctuation_or_numbering(text):
    """Check if text is isolated punctuation or numbering that shouldn't be part of title"""
    text = text.strip()
    if not text:
        return False
    
    # Single punctuation marks
    if len(text) == 1 and text in '.,;:!?()[]{}"\'-_=+*&^%$#@~`|\\/<>':
        return True
    
    # Multiple dots (leaders)
    if re.match(r'^\.{2,}$', text):
        return True
    
    # 【关键修复】缩小判断范围，避免误伤正文中的数字
    # 只有非常短的编号（1-2位数字+可选点号）才认为是编号
    if re.match(r'^\d{1,2}\.?$', text) and len(text) <= 3:
        return True
    
    # Page number patterns that might be misclassified
    if re.match(r'^\.\d+$', text):
        return True
    
    return False

def is_likely_page_number(text):
    """Check if text is likely a page number"""
    text = text.strip()
    if not text:
        return False
    
    # 【关键修复】提高判断标准，避免误判正文中的小数字
    # 纯数字且长度较短（1-3位）才可能是页码
    if text.isdigit():
        num_val = safe_convert_to_int(text)
        # 通常页码在1-999之间，且字符长度不超过3
        if 1 <= num_val <= 999 and len(text) <= 3:
            return True
        return False
    
    # Roman numerals
    if re.match(r'^[ivxlcdm]+$', text.lower()) or re.match(r'^[IVXLCDM]+$', text):
        return True
    
    # Page number with prefix/suffix (like "- 5 -", "Page 5", etc.)
    if re.match(r'^[-\s]*\d+[-\s]*$', text):
        return True
    
    # Page number with dot prefix (like ".57", ".123")
    if re.match(r'^\.\d+$', text):
        return True
    
    # Page number with various separators (like "...57", "..57", etc.)
    if re.match(r'^\.{2,}\d+$', text):
        return True
    
    # Page number in parentheses or brackets
    if re.match(r'^[\(\[\{]\s*\d+\s*[\)\]\}]$', text):
        return True
    
    # Page number with surrounding dashes or spaces
    if re.match(r'^[-\s\.]*\d{1,3}[-\s\.]*$', text):
        return True
    
    return False

def is_dot_leader(text):
    """Check if text consists of dot leaders or similar"""
    text = text.strip()
    if not text:
        return False
    
    # Mostly dots with possible spaces
    dot_count = text.count('.')
    total_chars = len(text.replace(' ', '').replace('\t', ''))
    
    if total_chars > 0 and dot_count / total_chars > 0.7:  # More than 70% dots
        return True
    
    return False

def extract_paragraph_text_with_variables(paragraph, namespaces, numbering_info=None, exclude_textbox_runs=True):
    """Extract paragraph text including page variables and formulas but excluding textbox content"""
    # Use the enhanced formula-aware extraction function
    return process_paragraph_element_with_formulas(paragraph, namespaces)

def process_field_run(run, namespaces, run_idx):
    """Process a run containing field characters or field instructions"""
    fld_chars = run.xpath('.//w:fldChar', namespaces=namespaces)
    instr_texts = run.xpath('.//w:instrText', namespaces=namespaces)
    
    if fld_chars:
        fld_char_type = fld_chars[0].get(f'{{{namespaces["w"]}}}fldCharType', '')
        if fld_char_type == 'begin':
            return {
                'type': 'field_begin',
                'run_index': run_idx,
                'display_text': '{{FIELD_BEGIN}}',
                'element_info': 'fldChar_begin'
            }
        elif fld_char_type == 'end':
            return {
                'type': 'field_end',
                'run_index': run_idx,
                'display_text': '{{FIELD_END}}',
                'element_info': 'fldChar_end'
            }
        elif fld_char_type == 'separate':
            return {
                'type': 'field_separate',
                'run_index': run_idx,
                'display_text': '{{FIELD_SEP}}',
                'element_info': 'fldChar_separate'
            }
    
    if instr_texts:
        instr_text = instr_texts[0].text if instr_texts[0].text else ''
        # Create a readable placeholder for common field instructions
        if 'PAGE' in instr_text:
            display_text = '{{PAGE_NUMBER}}'
        elif 'NUMPAGES' in instr_text:
            display_text = '{{TOTAL_PAGES}}'
        elif 'DATE' in instr_text:
            display_text = '{{DATE}}'
        elif 'TIME' in instr_text:
            display_text = '{{TIME}}'
        else:
            display_text = f'{{FIELD:{instr_text}}}'
        
        return {
            'type': 'field_instruction',
            'run_index': run_idx,
            'display_text': display_text,
            'element_info': f'instrText:{instr_text}',
            'instruction': instr_text
        }
    
    return None

def process_simple_field_run(run, namespaces, run_idx):
    """Process a run containing simple fields"""
    fld_simples = run.xpath('.//w:fldSimple', namespaces=namespaces)
    
    if fld_simples:
        instr = fld_simples[0].get(f'{{{namespaces["w"]}}}instr', '')
        
        # Create a readable placeholder for common field instructions
        if 'PAGE' in instr:
            display_text = '{{PAGE_NUMBER}}'
        elif 'NUMPAGES' in instr:
            display_text = '{{TOTAL_PAGES}}'
        elif 'DATE' in instr:
            display_text = '{{DATE}}'
        elif 'TIME' in instr:
            display_text = '{{TIME}}'
        else:
            display_text = f'{{FIELD:{instr}}}'
        
        # Store the complete original field element for exact reconstruction
        original_field_xml = etree.tostring(fld_simples[0], encoding='unicode')
        
        return {
            'type': 'simple_field',
            'run_index': run_idx,
            'display_text': display_text,
            'element_info': f'fldSimple:{instr}',
            'instruction': instr,
            'original_field_xml': original_field_xml
        }
    
    return None

def extract_textbox_content(tree, namespaces):
    """Extract content from all textboxes in the document, avoiding duplication - fixed indexing"""
    textbox_items = []
    
    # Find all textboxes in the document (both new format and VML fallback)
    wps_textboxes = tree.xpath('.//wps:txbx', namespaces=namespaces)
    vml_textboxes = tree.xpath('.//v:textbox', namespaces=namespaces)
    
    # Process WPS textboxes
    for textbox_idx, textbox in enumerate(wps_textboxes):
        textbox_item = process_single_textbox(textbox, textbox_idx, "wps", tree, namespaces)
        if textbox_item:
            textbox_items.append(textbox_item)
    
    # Process VML textboxes (avoid duplication by checking if they have corresponding WPS version)
    # Create a filtered list first to ensure correct indexing
    filtered_vml_textboxes = []
    for textbox in vml_textboxes:
        # Check if this is a fallback textbox (has corresponding WPS version)
        parent_alternateContent = textbox.xpath('ancestor::mc:AlternateContent', namespaces=namespaces)
        if not parent_alternateContent:
            filtered_vml_textboxes.append(textbox)
    
    # Now process the filtered list with correct indexing
    for textbox_idx, textbox in enumerate(filtered_vml_textboxes):
        textbox_item = process_single_textbox(textbox, textbox_idx, "vml", tree, namespaces)
        if textbox_item:
            textbox_items.append(textbox_item)
    
    return textbox_items

def process_single_textbox(textbox, textbox_idx, textbox_format, tree, namespaces):
    """Process a single textbox (either WPS or VML format)"""
    # Get textbox content
    if textbox_format == "wps":
        textbox_content = textbox.xpath('.//w:txbxContent', namespaces=namespaces)
    else:  # vml
        textbox_content = textbox.xpath('.//w:txbxContent', namespaces=namespaces)
    
    if not textbox_content:
        return None
    
    # Determine textbox type and positioning
    textbox_type = "inline"
    positioning_info = {}
    paragraph_context = None
    
    # Find parent drawing element
    parent_drawing = textbox.xpath('ancestor::w:drawing', namespaces=namespaces)
    parent_pict = textbox.xpath('ancestor::w:pict', namespaces=namespaces)
    
    if parent_drawing:
        # Check if it's inline or floating
        anchor_elements = parent_drawing[0].xpath('.//wp:anchor', namespaces=namespaces)
        inline_elements = parent_drawing[0].xpath('.//wp:inline', namespaces=namespaces)
        
        if anchor_elements:
            textbox_type = "floating"
            anchor = anchor_elements[0]
            
            # Extract positioning information
            position_h = anchor.xpath('.//wp:positionH', namespaces=namespaces)
            position_v = anchor.xpath('.//wp:positionV', namespaces=namespaces)
            wrap_elements = anchor.xpath('.//wp:wrapSquare | .//wp:wrapTopAndBottom | .//wp:wrapNone', namespaces=namespaces)
            
            if position_h:
                positioning_info['horizontal'] = {
                    'relative_from': position_h[0].get('relativeFrom'),
                    'align': position_h[0].xpath('.//wp:align', namespaces=namespaces)[0].text if position_h[0].xpath('.//wp:align', namespaces=namespaces) else None,
                    'pos_offset': position_h[0].xpath('.//wp:posOffset', namespaces=namespaces)[0].text if position_h[0].xpath('.//wp:posOffset', namespaces=namespaces) else None
                }
            
            if position_v:
                positioning_info['vertical'] = {
                    'relative_from': position_v[0].get('relativeFrom'),
                    'align': position_v[0].xpath('.//wp:align', namespaces=namespaces)[0].text if position_v[0].xpath('.//wp:align', namespaces=namespaces) else None,
                    'pos_offset': position_v[0].xpath('.//wp:posOffset', namespaces=namespaces)[0].text if position_v[0].xpath('.//wp:posOffset', namespaces=namespaces) else None
                }
            
            if wrap_elements:
                wrap_type = wrap_elements[0].tag.split('}')[-1]
                positioning_info['wrap_type'] = wrap_type
        
        # Find the paragraph that contains this textbox
        parent_paragraph = parent_drawing[0].xpath('ancestor::w:p', namespaces=namespaces)
        if parent_paragraph:
            # Find the index of this paragraph in the main document
            all_main_elements = get_all_body_elements(tree, namespaces)
            for elem_idx, elem in enumerate(all_main_elements):
                if elem == parent_paragraph[0]:
                    paragraph_context = elem_idx
                    break
    
    elif parent_pict:
        # VML textbox
        textbox_type = "floating"  # Assume VML textboxes are floating
        
        # Find the paragraph that contains this textbox
        parent_paragraph = parent_pict[0].xpath('ancestor::w:p', namespaces=namespaces)
        if parent_paragraph:
            # Find the index of this paragraph in the main document
            all_main_elements = get_all_body_elements(tree, namespaces)
            for elem_idx, elem in enumerate(all_main_elements):
                if elem == parent_paragraph[0]:
                    paragraph_context = elem_idx
                    break
    
    # Extract text content from textbox
    textbox_paragraphs = textbox_content[0].xpath('.//w:p', namespaces=namespaces)
    textbox_text = ""
    textbox_field_info = []
    
    for para_idx, paragraph in enumerate(textbox_paragraphs):
        # Enhanced TOC detection for textbox content
        is_toc, toc_info = detect_toc_paragraph_enhanced(paragraph, namespaces, False)
        
        if is_toc:
            toc_title_text, toc_structure = extract_toc_title_with_complete_structure_enhanced(paragraph, namespaces)
            para_text = toc_title_text
            para_field_info = None
        else:
            # Extract text from paragraph including formulas (don't exclude textbox runs since we're already processing textbox)
            para_text, para_field_info = process_paragraph_element_with_formulas(paragraph, namespaces)
            toc_structure = None
        
        if para_text:
            textbox_text += para_text
            if para_idx < len(textbox_paragraphs) - 1:
                textbox_text += "\n"
            
            if para_field_info:
                textbox_field_info.extend(para_field_info)
    
    if textbox_text.strip() and should_translate_enhanced(textbox_text):
        textbox_item = {
            "type": "textbox",
            "textbox_type": textbox_type,
            "textbox_format": textbox_format,
            "textbox_index": textbox_idx,
            "positioning_info": positioning_info,
            "paragraph_context": paragraph_context,
            "value": textbox_text.replace("\n", "␊").replace("\r", "␍")
        }
        
        if textbox_field_info:
            textbox_item["field_info"] = textbox_field_info
        
        app_logger.debug(f"Extracted textbox {textbox_idx}: '{textbox_text[:50]}...'")
        return textbox_item
    
    return None

def extract_numbering_translatable_content(numbering_xml, namespaces):
    """Extract translatable content from numbering.xml, preserving variable placeholders"""
    translatable_items = []
    
    if not numbering_xml:
        return translatable_items
    
    try:
        numbering_tree = etree.fromstring(numbering_xml)
        
        # Extract translatable text from abstractNum definitions
        abstract_nums = numbering_tree.xpath('//w:abstractNum', namespaces=namespaces)
        for abstract_num in abstract_nums:
            abstract_num_id = abstract_num.get(f'{{{namespaces["w"]}}}abstractNumId')
            
            # Process each level in the abstract numbering
            levels = abstract_num.xpath('.//w:lvl', namespaces=namespaces)
            for level in levels:
                level_id = level.get(f'{{{namespaces["w"]}}}ilvl')
                
                # Extract lvlText which might contain translatable content
                lvl_text_nodes = level.xpath('.//w:lvlText', namespaces=namespaces)
                for lvl_text_node in lvl_text_nodes:
                    lvl_text_val = lvl_text_node.get(f'{{{namespaces["w"]}}}val', '')
                    
                    # Check if this level text contains translatable content
                    if lvl_text_val and contains_translatable_content(lvl_text_val):
                        # Create special instruction for translator to preserve variables
                        translation_instruction = create_translation_instruction_for_numbering(lvl_text_val)
                        
                        translatable_items.append({
                            "type": "numbering_level_text",
                            "abstract_num_id": abstract_num_id,
                            "level_id": level_id,
                            "original_lvl_text": lvl_text_val,
                            "element_xpath": f'//w:abstractNum[@w:abstractNumId="{abstract_num_id}"]//w:lvl[@w:ilvl="{level_id}"]//w:lvlText',
                            "value": translation_instruction,
                            "preserve_variables": True
                        })
                        app_logger.debug(f"Extracted numbering level text: '{lvl_text_val}' -> '{translation_instruction}'")
                
                # Extract text from w:t elements within the level
                text_nodes = level.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    if text_node.text and text_node.text.strip():
                        text_content = text_node.text.strip()
                        if should_translate_enhanced(text_content):
                            translatable_items.append({
                                "type": "numbering_text_node",
                                "abstract_num_id": abstract_num_id,
                                "level_id": level_id,
                                "text_node_path": get_element_path(text_node, numbering_tree),
                                "original_text": text_content,
                                "value": text_content.replace("\n", "␊").replace("\r", "␍")
                            })
                            app_logger.debug(f"Extracted numbering text node: '{text_content}'")
        
    except Exception as e:
        app_logger.error(f"Error extracting translatable content from numbering.xml: {e}")
    
    return translatable_items

def contains_translatable_content(lvl_text_val):
    """Check if level text contains content that needs translation"""
    if not lvl_text_val:
        return False
    
    # Extract non-variable parts
    text_without_variables = re.sub(r'%\d+', '', lvl_text_val)
    text_without_punctuation = re.sub(r'[.\-:;,()[\]{}]', '', text_without_variables)
    cleaned_text = re.sub(r'\s+', ' ', text_without_punctuation).strip()
    
    if len(cleaned_text) > 0 and not cleaned_text.isdigit():
        has_meaningful_content = any(
            c.isalpha() or 
            '\u4e00' <= c <= '\u9fff' or  # Chinese
            '\u3131' <= c <= '\u318e' or  # Korean Jamo
            '\uac00' <= c <= '\ud7a3' or  # Korean Hangul
            '\u3040' <= c <= '\u309f' or  # Japanese Hiragana
            '\u30a0' <= c <= '\u30ff'     # Japanese Katakana
            for c in cleaned_text
        )
        return has_meaningful_content
    
    return False

def create_translation_instruction_for_numbering(lvl_text_val):
    """Create translation instruction that preserves variable placeholders"""
    if not lvl_text_val:
        return lvl_text_val
    
    variables = re.findall(r'%\d+', lvl_text_val)
    instruction = f"Translate this numbering format while preserving ALL variable placeholders exactly as they are: '{lvl_text_val}'"
    
    if variables:
        variables_str = ", ".join(variables)
        instruction += f" (Keep these variables unchanged: {variables_str})"
    
    return lvl_text_val

def get_element_path(element, root):
    """Get XPath-like path for an element within the document"""
    path = []
    current = element
    
    while current is not None and current != root:
        parent = current.getparent()
        if parent is not None:
            siblings = [child for child in parent if child.tag == current.tag]
            if len(siblings) > 1:
                index = siblings.index(current)
                path.append(f"{current.tag.split('}')[-1]}[{index}]")
            else:
                path.append(current.tag.split('}')[-1])
        else:
            path.append(current.tag.split('}')[-1])
        current = parent
    
    return "/".join(reversed(path))

def parse_numbering_xml(numbering_xml, namespaces):
    """Parse numbering.xml to understand numbering definitions"""
    numbering_info = {}
    
    if not numbering_xml:
        return numbering_info
    
    try:
        numbering_tree = etree.fromstring(numbering_xml)
        
        # Parse abstractNum definitions
        abstract_nums = numbering_tree.xpath('//w:abstractNum', namespaces=namespaces)
        for abstract_num in abstract_nums:
            abstract_num_id = abstract_num.get(f'{{{namespaces["w"]}}}abstractNumId')
            if abstract_num_id:
                numbering_info[f'abstract_{abstract_num_id}'] = {
                    'type': 'abstract',
                    'levels': {}
                }
                
                # Parse levels
                levels = abstract_num.xpath('.//w:lvl', namespaces=namespaces)
                for level in levels:
                    level_id = level.get(f'{{{namespaces["w"]}}}ilvl')
                    if level_id:
                        level_info = {
                            'numFmt': None,
                            'lvlText': None,
                            'start': None
                        }
                        
                        # Get number format
                        numFmt = level.xpath('.//w:numFmt', namespaces=namespaces)
                        if numFmt:
                            level_info['numFmt'] = numFmt[0].get(f'{{{namespaces["w"]}}}val')
                        
                        # Get level text
                        lvlText = level.xpath('.//w:lvlText', namespaces=namespaces)
                        if lvlText:
                            level_info['lvlText'] = lvlText[0].get(f'{{{namespaces["w"]}}}val')
                        
                        # Get start value
                        start = level.xpath('.//w:start', namespaces=namespaces)
                        if start:
                            level_info['start'] = start[0].get(f'{{{namespaces["w"]}}}val')
                        
                        numbering_info[f'abstract_{abstract_num_id}']['levels'][level_id] = level_info
        
        # Parse num definitions
        nums = numbering_tree.xpath('//w:num', namespaces=namespaces)
        for num in nums:
            num_id = num.get(f'{{{namespaces["w"]}}}numId')
            if num_id:
                abstract_num_id_refs = num.xpath('.//w:abstractNumId', namespaces=namespaces)
                if abstract_num_id_refs:
                    abstract_num_id = abstract_num_id_refs[0].get(f'{{{namespaces["w"]}}}val')
                    numbering_info[f'num_{num_id}'] = {
                        'type': 'num',
                        'abstractNumId': abstract_num_id
                    }
        
    except Exception as e:
        app_logger.error(f"Error parsing numbering.xml: {e}")
    
    return numbering_info

def extract_paragraph_numbering_info(numPr_element, numbering_info, namespaces):
    """Extract numbering information from paragraph's numPr element"""
    if numPr_element is None:
        return None
    
    result = {
        'has_numbering': True,
        'numId': None,
        'ilvl': None,
        'numbering_definition': None
    }
    
    # Extract numId
    numId_nodes = numPr_element.xpath('.//w:numId', namespaces=namespaces)
    if numId_nodes:
        result['numId'] = numId_nodes[0].get(f'{{{namespaces["w"]}}}val')
    
    # Extract ilvl (level)
    ilvl_nodes = numPr_element.xpath('.//w:ilvl', namespaces=namespaces)
    if ilvl_nodes:
        result['ilvl'] = ilvl_nodes[0].get(f'{{{namespaces["w"]}}}val')
    
    # Look up numbering definition
    if result['numId'] and numbering_info:
        num_key = f"num_{result['numId']}"
        if num_key in numbering_info:
            abstract_num_id = numbering_info[num_key].get('abstractNumId')
            if abstract_num_id:
                abstract_key = f"abstract_{abstract_num_id}"
                if abstract_key in numbering_info:
                    result['numbering_definition'] = numbering_info[abstract_key]
    
    return result

def is_numbering_run(run, namespaces, numbering_info=None):
    """Check if a run contains only numbering text that should be excluded"""
    if not numbering_info or not numbering_info.get('has_numbering'):
        return False
    
    # Get text from the run
    text_nodes = run.xpath('.//w:t', namespaces=namespaces)
    run_text = ''.join(node.text or '' for node in text_nodes).strip()
    
    if not run_text:
        return False
    
    # Check for common numbering patterns
    numbering_patterns = [
        r'^\d+\.$',  # 1.
        r'^\d+\)$',  # 1)
        r'^[a-zA-Z]\.$',  # a.
        r'^[a-zA-Z]\)$',  # a)
        r'^[ivxlcdm]+\.$',  # i., ii., iii., etc.
        r'^[IVXLCDM]+\.$',  # I., II., III., etc.
        r'^•$',  # bullet
        r'^-$',  # dash
        r'^\*$',  # asterisk
    ]
    
    for pattern in numbering_patterns:
        if re.match(pattern, run_text):
            return True
    
    return False

def remove_leading_numbering_patterns(text):
    """Remove leading numbering patterns from text, but preserve dates, section numbers, and other valid content"""
    if not text:
        return text
    
    # Check if the text looks like a date first - if so, don't remove anything
    if is_likely_date_format(text.strip()):
        return text
    
    # Check if this looks like a section number (e.g., 1.1, 2.0, 3.2.1, etc.)
    if is_likely_section_number(text.strip()):
        return text
    
    # Patterns to remove from the beginning of text
    patterns = [
        r'^\d{1,3}\.\s+',  # 1. 12. 123. (require at least one space after the dot)
        r'^\d+\)\s+',  # 1) (require at least one space after)
        r'^[a-zA-Z]\.\s+',  # a. (require at least one space after)
        r'^[a-zA-Z]\)\s+',  # a) (require at least one space after)
        r'^[ivxlcdm]+\.\s+',  # i., ii., iii., etc. (require at least one space after)
        r'^[IVXLCDM]+\.\s+',  # I., II., III., etc. (require at least one space after)
        r'^•\s*',  # bullet
        r'^-\s*',  # dash
        r'^\*\s*',  # asterisk
    ]
    
    for pattern in patterns:
        # Only apply the pattern if what remains after removal would still have meaningful content
        potential_result = re.sub(pattern, '', text, count=1)
        if potential_result.strip() and len(potential_result.strip()) > 0:
            text = potential_result
        break  # Only apply the first matching pattern
    
    return text

def is_likely_section_number(text):
    """Check if text starts with a section number format like 1.1, 2.0, 3.2.1, etc."""
    if not text:
        return False
    
    # Section number patterns that should be preserved
    section_patterns = [
        r'^\d+\.\d+(?:\.\d+)*(?:\s|$)',  # 1.1 text, 2.0 text, 1.2.3 text, etc.
        r'^\d+\.\d+(?:\.\d+)*\w',        # 1.1text, 2.0something (no space but followed by word)
    ]
    
    for pattern in section_patterns:
        if re.match(pattern, text):
            app_logger.debug(f"Detected section number pattern in text: '{text[:50]}...'")
            return True
    
    return False

def is_likely_date_format(text):
    """Check if text looks like a date format and should not be treated as numbering"""
    if not text:
        return False
    
    # Common date patterns
    date_patterns = [
        r'^\d{4}\.\d{1,2}\.\d{1,2}$',  # 2022.12.31
        r'^\d{4}-\d{1,2}-\d{1,2}$',   # 2022-12-31
        r'^\d{4}/\d{1,2}/\d{1,2}$',   # 2022/12/31
        r'^\d{1,2}\.\d{1,2}\.\d{4}$', # 31.12.2022
        r'^\d{1,2}-\d{1,2}-\d{4}$',   # 31-12-2022
        r'^\d{1,2}/\d{1,2}/\d{4}$',   # 31/12/2022
        r'^\d{1,2}\.\d{1,2}\.\d{2}$', # 31.12.22
        r'^\d{4}\.\d{1,2}$',          # 2022.12
        r'^\d{1,2}\.\d{4}$',          # 12.2022
    ]
    
    for pattern in date_patterns:
        if re.match(pattern, text):
            return True
    
    return False

def should_translate_enhanced(text):
    """Enhanced translation check - more inclusive than original, with date handling"""
    if not text or not text.strip():
        return False
    
    # Clean brackets first before any analysis
    text_for_analysis = clean_translation_brackets(text.strip())
    
    # Remove field placeholders and formula placeholders for analysis
    clean_text = re.sub(r'\{\{[^}]+\}\}', '', text_for_analysis)
    clean_text = re.sub(r'\[formula_\d+\]', '', clean_text)
    clean_text = clean_text.strip()
    
    # Skip very short text (likely symbols or numbers only)
    if len(clean_text) < 1:
        return False
    
    # Skip pure numbers (but allow dates)
    if clean_text.isdigit():
        return False
    
    # Check for date patterns - these should be translated
    # Use the cleaned text without brackets for date detection
    date_patterns = [
        # ISO and standard formats with separators (移除单词边界，使用更宽松的匹配)
        r'\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}',              # YYYY.M.D, YYYY-M-D, YYYY/M/D (包括2021.4.1, 2022.12.31)
        r'\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}',              # M.D.YYYY, M/D/YYYY, D.M.YYYY  
        r'\d{4}[.\-/]\d{1,2}',                            # YYYY.M, YYYY/M, YYYY-M
        r'\d{1,2}[.\-/]\d{4}',                            # M/YYYY, M-YYYY
        
        # Chinese date formats
        r'\d{4}年\d{1,2}月\d{1,2}日',                      # 2024年12月31日
        r'\d{4}年\d{1,2}月',                              # 2024年12月
        r'\d{1,2}月\d{1,2}日',                            # 12月31日
        r'\d{4}年',                                       # 2024年
        r'\d{1,2}月',                                     # 12月
        
        # English month names (full)
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}',  # January 1, 2024
        r'\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}',   # 1 January 2024
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}',             # January 2024
        
        # English month abbreviations
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s*\d{4}',  # Jan 1, 2024 or Jan. 1, 2024
        r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}',   # 1 Jan 2024
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}',             # Jan 2024
        
        # Ordinal dates
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th),?\s*\d{4}',  # January 1st, 2024
        r'\d{1,2}(?:st|nd|rd|th)\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}',   # 1st January 2024
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th),?\s*\d{4}',  # Jan 1st, 2024
        r'\d{1,2}(?:st|nd|rd|th)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{4}',   # 1st Jan 2024
        
        # Time formats
        r'\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?',  # 14:30, 2:30 PM, 14:30:25
        r'\d{1,2}时\d{1,2}分(?:\d{1,2}秒)?',              # 14时30分25秒
        
        # Week/day patterns
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)',     # Full day names
        r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?',                              # Day abbreviations
        r'(?:周一|周二|周三|周四|周五|周六|周日|星期一|星期二|星期三|星期四|星期五|星期六|星期日)',  # Chinese weekdays
        
        # Additional formats (with separators only)
        r'\d{1,2}/\d{1,2}',                              # M/D or D/M (without year)
        r'\d{1,2}-\d{1,2}',                              # M-D or D-M (without year)
        r'第\d+周',                                       # 第1周 (Chinese week format)
        r'Q[1-4]\s*\d{4}',                               # Q1 2024 (Quarter)
        r'\d{4}Q[1-4]',                                  # 2024Q1
        r'第[一二三四]季度',                                # 第一季度 (Chinese quarter)
    ]
    
    for pattern in date_patterns:
        if re.search(pattern, clean_text, re.IGNORECASE):
            app_logger.debug(f"Date pattern detected in text: '{clean_text}', marking for translation")
            return True
    
    # Skip pure punctuation
    if all(c in '.,;:!?()[]{}"\'-_=+*&^%$#@~`|\\/<>' for c in clean_text):
        return False
    
    # Check if text contains meaningful content (letters, CJK characters, etc.)
    has_meaningful_content = any(c.isalpha() or '\u4e00' <= c <= '\u9fff' or '\u3131' <= c <= '\u318e' or '\uac00' <= c <= '\ud7a3' for c in clean_text)
    
    if has_meaningful_content:
        return True
    
    # Fallback to original should_translate if available
    try:
        return should_translate(text)
    except:
        return True  # Default to translate if function fails


def write_translated_content_to_word(file_path, original_json_path, translated_json_path, save_temp_dir, result_dir):
    """Write translated content back to Word document in bilingual format (original followed by translation)"""
    
    # Load translation data
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    translations = {}
    for item in translated_data:
        item_id = str(item.get("id", item.get("count_src")))
        if item_id and "translated" in item:
            translations[item_id] = item["translated"]
    
    app_logger.info(f"Loaded {len(translations)} translations")
    
    # Get temp directory path
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(save_temp_dir, filename)
    temp_dir_info_path = os.path.join(temp_folder, "temp_dir_path.txt")
    
    temp_dir = None
    if os.path.exists(temp_dir_info_path):
        with open(temp_dir_info_path, "r", encoding="utf-8") as f:
            temp_dir = f.read().strip()
    
    if not temp_dir or not os.path.exists(temp_dir):
        # Fallback: create new temp directory
        temp_dir = tempfile.mkdtemp()
        with ZipFile(file_path, 'r') as docx:
            docx.extractall(temp_dir)
    
    try:
        # Complete namespaces including math
        namespaces = {
            'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
            'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
            'wps': 'http://schemas.microsoft.com/office/word/2010/wordprocessingShape',
            'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006',
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
            'v': 'urn:schemas-microsoft-com:vml',
            'w10': 'urn:schemas-microsoft-com:office:word',
            'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
            'w15': 'http://schemas.microsoft.com/office/word/2012/wordml',
            'wp14': 'http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing',
            'wpc': 'http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas',
            'wpg': 'http://schemas.microsoft.com/office/word/2010/wordprocessingGroup',
            'wpi': 'http://schemas.microsoft.com/office/word/2010/wordprocessingInk',
            'wne': 'http://schemas.microsoft.com/office/word/2006/wordml',
            'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
            'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram',
            'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math'  # Math namespace
        }
        
        # Load and update main document
        document_xml_path = os.path.join(temp_dir, 'word', 'document.xml')
        with open(document_xml_path, 'rb') as f:
            document_xml = f.read()
        document_tree = etree.fromstring(document_xml)
        
        # Load and update numbering.xml if exists
        numbering_tree = None
        numbering_xml_path = os.path.join(temp_dir, 'word', 'numbering.xml')
        if os.path.exists(numbering_xml_path):
            with open(numbering_xml_path, 'rb') as f:
                numbering_xml = f.read()
            numbering_tree = etree.fromstring(numbering_xml)
            numbering_info = parse_numbering_xml(numbering_xml, namespaces)
            update_numbering_xml_with_translations(numbering_tree, original_data, translations, namespaces)
        
        # Load and update footnotes.xml if exists
        footnotes_tree = None
        footnotes_xml_path = os.path.join(temp_dir, 'word', 'footnotes.xml')
        if os.path.exists(footnotes_xml_path):
            app_logger.info("Processing footnotes.xml")
            with open(footnotes_xml_path, 'rb') as f:
                footnotes_xml = f.read()
            footnotes_tree = etree.fromstring(footnotes_xml)
            
            # Count footnote translations available
            footnote_translations = [item for item in original_data if item["type"] == "footnote"]
            app_logger.info(f"Found {len(footnote_translations)} footnote items to translate")
            
            update_footnotes_with_bilingual_format(footnotes_tree, original_data, translations, namespaces)
        else:
            app_logger.info("No footnotes.xml found in document")
        
        # Load header/footer files
        header_footer_trees = {}
        word_dir = os.path.join(temp_dir, 'word')
        if os.path.exists(word_dir):
            for filename in os.listdir(word_dir):
                if filename.startswith('header') or filename.startswith('footer'):
                    filepath = os.path.join(word_dir, filename)
                    with open(filepath, 'rb') as f:
                        hf_content = f.read()
                    header_footer_trees[f'word/{filename}'] = etree.fromstring(hf_content)
        
        # Get all SDT elements
        all_sdt_elements = document_tree.xpath('.//w:sdt', namespaces=namespaces)
        
        # Get all document elements
        all_main_elements = get_all_body_elements(document_tree, namespaces)
        
        # Get all textboxes for processing - use same logic as extraction to ensure index consistency
        all_wps_textboxes = document_tree.xpath('.//wps:txbx', namespaces=namespaces)
        all_vml_textboxes = []
        vml_textbox_candidates = document_tree.xpath('.//v:textbox', namespaces=namespaces)
        for textbox in vml_textbox_candidates:
            # Check if this is a fallback textbox (has corresponding WPS version)
            parent_alternateContent = textbox.xpath('ancestor::mc:AlternateContent', namespaces=namespaces)
            if parent_alternateContent:
                # This is a fallback, skip it as we already processed the WPS version
                continue
            all_vml_textboxes.append(textbox)

        # Apply SmartArt translations with bilingual format
        smartart_items = [item for item in original_data if item['type'] == 'smartart']
        if smartart_items:
            apply_smartart_translations_bilingual(temp_dir, smartart_items, translations, namespaces)

        # Process translations in BILINGUAL FORMAT
        for item in original_data:
            item_id = str(item.get("id", item.get("count_src")))
            translated_text = translations.get(item_id)
            
            if not translated_text:
                continue
                
            # Skip numbering, smartart, and footnote items as they're handled separately
            if item["type"] in ["numbering_level_text", "numbering_text_node", "smartart", "footnote"]:
                continue
                
            translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
            original_text = item.get("value", "").replace("␊", "\n").replace("␍", "\r")
            
            # Create bilingual text (original + newline + translation)
            bilingual_text = create_bilingual_text(original_text, translated_text)
            
            if item["type"] == "sdt_paragraph":
                update_sdt_paragraph_with_bilingual_format(
                    item, bilingual_text, all_sdt_elements, namespaces
                )
            
            elif item["type"] == "sdt_table_cell":
                update_sdt_table_cell_with_bilingual_format(
                    item, bilingual_text, all_sdt_elements, namespaces
                )
            
            elif item["type"] == "paragraph":
                update_paragraph_with_bilingual_format(
                    item, bilingual_text, all_main_elements, namespaces
                )
                
            elif item["type"] == "table_cell":
                update_table_cell_with_bilingual_format(
                    item, bilingual_text, all_main_elements, namespaces
                )
            
            elif item["type"] == "textbox":
                update_textbox_with_bilingual_format(
                    item, bilingual_text, all_wps_textboxes, all_vml_textboxes, namespaces
                )
            
            elif item["type"] == "header_footer":
                update_header_footer_paragraph_with_bilingual_format(
                    item, bilingual_text, header_footer_trees, namespaces
                )
            
            elif item["type"] == "header_footer_textbox":
                update_header_footer_textbox_with_bilingual_format(
                    item, bilingual_text, header_footer_trees, namespaces
                )
            
            elif item["type"] == "header_footer_table_cell":
                update_header_footer_table_cell_with_bilingual_format(
                    item, bilingual_text, header_footer_trees, namespaces
                )

        # Save all modified files back to temp directory
        with open(document_xml_path, "wb") as f:
            f.write(etree.tostring(document_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
        
        if numbering_tree is not None:
            with open(numbering_xml_path, "wb") as f:
                f.write(etree.tostring(numbering_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
        
        if footnotes_tree is not None:
            app_logger.info("Saving updated footnotes.xml")
            with open(footnotes_xml_path, "wb") as f:
                f.write(etree.tostring(footnotes_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))
            app_logger.info("Successfully saved footnotes.xml")
        
        for hf_file, hf_tree in header_footer_trees.items():
            hf_path = os.path.join(temp_dir, hf_file.replace('/', os.sep))
            with open(hf_path, "wb") as f:
                f.write(etree.tostring(hf_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))

        # Create result file
        result_folder = result_dir
        os.makedirs(result_folder, exist_ok=True)
        result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.docx")

        # Create new DOCX file with all original files preserved
        with ZipFile(result_path, 'w', ZIP_DEFLATED) as new_doc:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    file_path_in_temp = os.path.join(root, file)
                    arcname = os.path.relpath(file_path_in_temp, temp_dir).replace(os.sep, '/')
                    new_doc.write(file_path_in_temp, arcname)

        app_logger.info(f"Bilingual Word document saved to: {result_path}")
        return result_path
        
    finally:
        # Clean up temp directory
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

def update_footnote_paragraph_with_bilingual_format(paragraph, bilingual_text, namespaces, field_info=None, original_structure=None):
    """Update footnote paragraph with bilingual format while preserving footnote reference markers"""
    
    # Get all runs in the paragraph
    all_runs = paragraph.xpath('./w:r', namespaces=namespaces)
    
    # Separate runs into different categories
    footnote_ref_runs = []
    text_runs = []
    field_runs = []
    other_runs = []
    
    for run in all_runs:
        if run.xpath('.//w:footnoteRef', namespaces=namespaces):
            # This run contains footnote reference marker, must be preserved
            footnote_ref_runs.append(run)
        elif run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces):
            # This run contains field information
            field_runs.append(run)
        elif run.xpath('.//w:t', namespaces=namespaces):
            # This run contains text content that can be replaced
            text_runs.append(run)
        else:
            # Other special runs (bookmarks, etc.)
            other_runs.append(run)
    
    # Remove only the text runs, preserving footnote reference runs and other special runs
    for run in text_runs:
        paragraph.remove(run)
    
    # Process bilingual text with formulas and fields
    if field_info:
        # Extract formulas from field_info
        formulas = []
        for item in field_info:
            if item.get('type') == 'formula' and item.get('xml_content'):
                try:
                    formula_element = etree.fromstring(item['xml_content'])
                    formulas.append(formula_element)
                except Exception as e:
                    app_logger.warning(f"Error parsing formula XML: {e}")
        
        update_paragraph_content_with_fields_and_formulas_bilingual(
            paragraph, bilingual_text, namespaces, field_info, formulas, original_structure
        )
    else:
        add_text_with_formulas_and_bilingual_formatting(paragraph, bilingual_text, namespaces, [], original_structure)

def update_footnotes_with_bilingual_format(footnotes_tree, original_data, translations, namespaces):
    """Update footnotes.xml with bilingual translated content"""
    
    # Get all footnotes except separator and continuation separator
    footnotes = footnotes_tree.xpath('//w:footnote[not(@w:type="separator") and not(@w:type="continuationSeparator")]', namespaces=namespaces)
    
    # Create a mapping of footnote_id to footnote element for faster lookup
    footnotes_by_id = {}
    for footnote in footnotes:
        footnote_id = footnote.get(f'{{{namespaces["w"]}}}id')
        if footnote_id:
            # Store both string and int versions to handle type mismatches
            footnotes_by_id[str(footnote_id)] = footnote
            footnotes_by_id[footnote_id] = footnote
    
    app_logger.info(f"Found {len(footnotes)} footnotes to process")
    
    # Process footnote translations
    updated_count = 0
    for item in original_data:
        if item["type"] != "footnote":
            continue
            
        item_id = str(item.get("id", item.get("count_src")))
        translated_text = translations.get(item_id)
        
        if not translated_text:
            app_logger.debug(f"No translation found for footnote item {item_id}")
            continue
            
        translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
        original_text = item.get("value", "").replace("␊", "\n").replace("␍", "\r")
        
        # Create bilingual text (original + newline + translation)
        bilingual_text = create_bilingual_text(original_text, translated_text)
        
        footnote_id = item.get("footnote_id")
        paragraph_index = item.get("paragraph_index")
        
        # Try both string and original footnote_id
        footnote = footnotes_by_id.get(str(footnote_id))
        if footnote is None:
            footnote = footnotes_by_id.get(footnote_id)
        
        if footnote is None:
            app_logger.error(f"Footnote ID {footnote_id} not found in footnotes. Available IDs: {list(set(k for k in footnotes_by_id.keys() if isinstance(k, str)))}")
            continue
            
        # Get paragraphs in the footnote
        footnote_paragraphs = footnote.xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        
        if paragraph_index >= len(footnote_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in footnote {footnote_id} (has {len(footnote_paragraphs)} paragraphs)")
            continue
            
        target_paragraph = footnote_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(target_paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            # Use specialized footnote paragraph update function
            update_footnote_paragraph_with_bilingual_format(
                target_paragraph, bilingual_text, namespaces, field_info, original_structure
            )
        
        updated_count += 1
        app_logger.info(f"Updated footnote {footnote_id}.{paragraph_index} with bilingual format: '{bilingual_text[:50]}...'")
    
    app_logger.info(f"Successfully updated {updated_count} footnotes with bilingual format")

def apply_smartart_translations_bilingual(temp_dir, smartart_items, translations, namespaces):
    """Apply translations to SmartArt diagrams in Word document with bilingual format."""
    if not smartart_items:
        return
    
    app_logger.info(f"Processing {len(smartart_items)} SmartArt translations in Word document")
    
    # Group items by diagram_index
    items_by_diagram = {}
    for item in smartart_items:
        diagram_index = item['diagram_index']
        if diagram_index not in items_by_diagram:
            items_by_diagram[diagram_index] = []
        items_by_diagram[diagram_index].append(item)
    
    for diagram_index, items in items_by_diagram.items():
        drawing_path = f"word/diagrams/drawing{diagram_index}.xml"
        data_path = f"word/diagrams/data{diagram_index}.xml"
        
        # Process drawing file
        try:
            drawing_file_path = os.path.join(temp_dir, drawing_path.replace('/', os.sep))
            if os.path.exists(drawing_file_path):
                with open(drawing_file_path, 'rb') as f:
                    drawing_xml = f.read()
                drawing_tree = etree.fromstring(drawing_xml)
                
                for item in items:
                    item_id = str(item.get("id", item.get("count_src")))
                    translated_text = translations.get(item_id)
                    
                    if not translated_text:
                        app_logger.warning(f"Missing translation for SmartArt item {item_id}")
                        continue
                    
                    translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                    original_text = item.get("value", "").replace("␊", "\n").replace("␍", "\r")
                    
                    # Create bilingual text (original + newline + translation)
                    bilingual_text = create_bilingual_text(original_text, translated_text)
                    
                    # Find the shape using shape_index
                    shapes_with_txbody = drawing_tree.xpath('.//dsp:sp[.//dsp:txBody]', namespaces=namespaces)
                    
                    if item['shape_index'] < len(shapes_with_txbody):
                        shape = shapes_with_txbody[item['shape_index']]
                        
                        # Find the txBody
                        tx_bodies = shape.xpath('.//dsp:txBody', namespaces=namespaces)
                        if item['tx_body_index'] < len(tx_bodies):
                            tx_body = tx_bodies[item['tx_body_index']]
                            
                            # Find the paragraph
                            paragraphs = tx_body.xpath('.//a:p', namespaces=namespaces)
                            if item['paragraph_index'] < len(paragraphs):
                                paragraph = paragraphs[item['paragraph_index']]
                                distribute_smartart_text_to_runs_bilingual(paragraph, bilingual_text, item, namespaces)
                                app_logger.info(f"Updated SmartArt drawing text for diagram {diagram_index}, shape {item['shape_index']} with bilingual format")
                
                # Save modified drawing
                with open(drawing_file_path, "wb") as f:
                    f.write(etree.tostring(drawing_tree, xml_declaration=True, 
                                          encoding="UTF-8", standalone="yes"))
                app_logger.info(f"Saved modified SmartArt drawing file: {drawing_path}")
                                                        
        except Exception as e:
            app_logger.error(f"Failed to apply SmartArt translation to {drawing_path}: {e}")
            continue
        
        # Process corresponding data file
        try:
            data_file_path = os.path.join(temp_dir, data_path.replace('/', os.sep))
            if os.path.exists(data_file_path):
                with open(data_file_path, 'rb') as f:
                    data_xml = f.read()
                data_tree = etree.fromstring(data_xml)
                
                for item in items:
                    item_id = str(item.get("id", item.get("count_src")))
                    translated_text = translations.get(item_id)
                    
                    if not translated_text:
                        continue
                    
                    translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                    original_text = item.get('original_text', '')
                    
                    # Create bilingual text (original + newline + translation)
                    bilingual_text = create_bilingual_text(original_text, translated_text)
                    
                    # Find all dgm:pt elements that contain text
                    points = data_tree.xpath('.//dgm:pt[.//a:t]', namespaces=namespaces)
                    
                    # Try to find matching text by content
                    for point in points:
                        point_paragraphs = point.xpath('.//a:p', namespaces=namespaces)
                        for p_idx, point_paragraph in enumerate(point_paragraphs):
                            # Get current text from this paragraph
                            point_text_runs = point_paragraph.xpath('.//a:r', namespaces=namespaces)
                            if point_text_runs:
                                point_run_info = process_smartart_text_runs(point_text_runs, namespaces)
                                # If the original text matches, update this paragraph
                                if point_run_info['merged_text'].strip() == original_text.strip():
                                    distribute_smartart_text_to_runs_bilingual(point_paragraph, bilingual_text, item, namespaces)
                                    app_logger.info(f"Updated SmartArt data text for diagram {diagram_index}: '{original_text}' -> bilingual format")
                                    break
                
                # Save modified data
                with open(data_file_path, "wb") as f:
                    f.write(etree.tostring(data_tree, xml_declaration=True, 
                                         encoding="UTF-8", standalone="yes"))
                app_logger.info(f"Saved modified SmartArt data file: {data_path}")
                                                     
        except Exception as e:
            app_logger.error(f"Failed to apply SmartArt translation to {data_path}: {e}")
            continue

def distribute_smartart_text_to_runs_bilingual(paragraph, bilingual_text, item, namespaces):
    """Distribute bilingual text across SmartArt runs, preserving spacing and structure."""
    text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
    
    if not text_runs:
        return
    
    original_run_texts = item.get('run_texts', [])
    original_run_lengths = item.get('run_lengths', [])
    
    # If we don't have the original structure, fallback to simple distribution
    if not original_run_texts or len(original_run_texts) != len(text_runs):
        app_logger.warning(f"Mismatch in SmartArt run structure, using simple distribution")
        simple_smartart_text_distribution_bilingual(text_runs, bilingual_text, namespaces)
        return
    
    # Use intelligent distribution based on original structure
    intelligent_smartart_text_distribution_bilingual(text_runs, bilingual_text, original_run_texts, original_run_lengths, namespaces)

def simple_smartart_text_distribution_bilingual(text_runs, bilingual_text, namespaces):
    """Simple fallback distribution method for SmartArt with bilingual format."""
    if not text_runs:
        return
    
    # Split bilingual text by lines
    lines = bilingual_text.split('\n')
    
    # Put first line in first run, second line with line break in second run if available
    for i, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if text_node:
            # Apply font settings for non-Chinese target languages
            apply_smartart_latin_font_to_run(text_run, namespaces)
            
            if i == 0 and len(lines) > 0:
                # First run gets original text
                text_node[0].text = lines[0]
            elif i == 1 and len(lines) > 1:
                # Second run gets translated text (with line break handled by separate runs)
                text_node[0].text = lines[1]
            else:
                # Clear other runs
                text_node[0].text = ""

def intelligent_smartart_text_distribution_bilingual(text_runs, bilingual_text, original_run_texts, original_run_lengths, namespaces):
    """Intelligent SmartArt text distribution that preserves spacing and structure while supporting bilingual format."""
    
    # Split bilingual text into original and translated parts
    lines = bilingual_text.split('\n')
    if len(lines) >= 2:
        original_part = lines[0]
        translated_part = lines[1]
    else:
        # Fallback if not proper bilingual format
        original_part = bilingual_text
        translated_part = bilingual_text
    
    # Calculate total length excluding empty runs
    meaningful_runs = [(i, length) for i, length in enumerate(original_run_lengths) if length > 0]
    total_meaningful_length = sum(length for _, length in meaningful_runs)
    
    if total_meaningful_length == 0:
        simple_smartart_text_distribution_bilingual(text_runs, bilingual_text, namespaces)
        return
    
    # For bilingual format, we need to decide how to distribute
    # Option 1: Replace original text with bilingual text in first meaningful run
    # Option 2: Try to distribute across runs
    
    # Use Option 1 for simplicity - put bilingual text in first meaningful run
    for run_index, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if not text_node:
            continue
            
        original_length = original_run_lengths[run_index] if run_index < len(original_run_lengths) else 0
        
        if original_length > 0:
            # Apply font settings for non-Chinese target languages
            apply_smartart_latin_font_to_run(text_run, namespaces)
            
            # First meaningful run gets bilingual text
            text_node[0].text = bilingual_text
            # Clear other meaningful runs
            for other_run_index, other_run in enumerate(text_runs[run_index + 1:], run_index + 1):
                if other_run_index < len(original_run_lengths) and original_run_lengths[other_run_index] > 0:
                    other_text_node = other_run.xpath('./a:t', namespaces=namespaces)
                    if other_text_node:
                        other_text_node[0].text = ""
            break
        else:
            # Empty run stays empty
            text_node[0].text = ""

def apply_smartart_latin_font_to_run(text_run, namespaces):
    """为SmartArt文本运行设置罗马字体（针对非中文目标语言）"""
    # 使用全局目标语言
    effective_target = globals().get('_current_target_language') or DateConversionConfig.TARGET_LANGUAGE
    
    # 如果目标语言是中文相关，不设置罗马字体
    if effective_target and effective_target.lower() in ['zh', 'zh-cn', 'zh-tw', 'chinese']:
        return
    
    # 查找或创建运行属性（SmartArt使用'a'命名空间）
    rPr = text_run.xpath('./a:rPr', namespaces=namespaces)
    if not rPr:
        rPr_element = etree.SubElement(text_run, f"{{{namespaces['a']}}}rPr")
    else:
        rPr_element = rPr[0]
    
    # 设置罗马字体
    # 查找现有的字体设置
    existing_fonts = rPr_element.xpath('./a:latin', namespaces=namespaces)
    if existing_fonts:
        latin_font = existing_fonts[0]
    else:
        latin_font = etree.SubElement(rPr_element, f"{{{namespaces['a']}}}latin")
    
    # 为非中文内容设置罗马字体
    font_name = "Times New Roman"  # 可以根据需要修改字体
    latin_font.set('typeface', font_name)

def create_bilingual_text(original_text, translated_text):
    """Create bilingual text format: original text + newline + translated text with automatic date conversion and footnote reference handling"""
    if not original_text:
        # 清理译文中的《》符号
        return clean_translation_brackets(translated_text)
    if not translated_text:
        return original_text
    
    # Handle cases where original or translated text already contains line breaks
    original_clean = original_text.strip()
    translated_clean = translated_text.strip()
    
    # 检测并转换译文中未翻译的日期格式
    converted_translated = detect_and_convert_untranslated_dates(
        original_clean, 
        translated_clean, 
        DateConversionConfig.TARGET_LANGUAGE
    )
    
    # 再次清理译文中的《》符号（虽然在detect_and_convert_untranslated_dates中已经清理了，但为了确保）
    converted_translated = clean_translation_brackets(converted_translated)
    
    # 处理脚注引用：只在原文中保留，译文中移除
    # 查找原文中的脚注引用占位符
    footnote_ref_pattern = r'\{\{FOOTNOTE_REF_\d+\}\}'
    footnote_refs = re.findall(footnote_ref_pattern, original_clean)
    
    if footnote_refs:
        # 从译文中移除所有脚注引用占位符
        for footnote_ref in footnote_refs:
            converted_translated = converted_translated.replace(footnote_ref, '')
        
        # 清理译文中可能产生的多余空格
        converted_translated = re.sub(r'\s+', ' ', converted_translated).strip()
        
        app_logger.debug(f"Removed footnote references from translation: {footnote_refs}")
    
    return f"{original_clean}\n{converted_translated}"

def update_sdt_paragraph_with_bilingual_format(item, bilingual_text, all_sdt_elements, namespaces):
    """Update SDT paragraph with bilingual format (original + translation)"""
    try:
        sdt_index = item.get("sdt_index")
        paragraph_index = item.get("paragraph_index")
        
        if sdt_index is None or sdt_index >= len(all_sdt_elements):
            app_logger.error(f"Invalid SDT index: {sdt_index}")
            return
        
        sdt = all_sdt_elements[sdt_index]
        sdt_content = sdt.xpath('./w:sdtContent', namespaces=namespaces)
        
        if not sdt_content:
            app_logger.error(f"No SDT content found for index: {sdt_index}")
            return
        
        paragraphs = sdt_content[0].xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        
        if paragraph_index >= len(paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in SDT")
            return
        
        paragraph = paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            # For TOC entries, handle bilingual format specially
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                paragraph, bilingual_text, namespaces, None, field_info, original_structure
            )
        
        app_logger.info(f"Updated SDT paragraph {sdt_index}.{paragraph_index} with bilingual format")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating SDT paragraph: {e}")

def update_sdt_table_cell_with_bilingual_format(item, bilingual_text, all_sdt_elements, namespaces):
    """Update SDT table cell with bilingual format"""
    try:
        sdt_index = item.get("sdt_index")
        table_index = item.get("table_index")
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        if sdt_index is None or sdt_index >= len(all_sdt_elements):
            app_logger.error(f"Invalid SDT index: {sdt_index}")
            return
        
        sdt = all_sdt_elements[sdt_index]
        sdt_content = sdt.xpath('./w:sdtContent', namespaces=namespaces)
        
        if not sdt_content:
            app_logger.error(f"No SDT content found for index: {sdt_index}")
            return
        
        # Handle nested table indices
        if isinstance(table_index, str) and "_nested_" in str(table_index):
            update_sdt_nested_table_cell_with_bilingual_format(
                item, bilingual_text, sdt_content[0], namespaces
            )
            return
        
        tables = sdt_content[0].xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        
        if table_index >= len(tables):
            app_logger.error(f"Table index {table_index} out of bounds in SDT")
            return
        
        table = tables[table_index]
        rows = table.xpath('./w:tr', namespaces=namespaces)
        
        if row_idx >= len(rows):
            app_logger.error(f"Row index {row_idx} out of bounds in SDT table")
            return
        
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Column index {col_idx} out of bounds in SDT table")
            return
        
        cell = cells[col_idx]
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in SDT table cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(target_paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                target_paragraph, bilingual_text, namespaces, None, field_info, original_structure
            )
        
        app_logger.info(f"Updated SDT table cell {sdt_index}.{table_index}.{row_idx}.{col_idx}.{paragraph_index} with bilingual format")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating SDT table cell: {e}")

def update_sdt_nested_table_cell_with_bilingual_format(item, bilingual_text, sdt_content, namespaces):
    """Update nested table cell within SDT with bilingual format - FIXED for multi-level nesting"""
    try:
        # Parse nested table identifier
        table_index_str = str(item.get("table_index"))
        
        # Split by "_nested_" to get each nesting level
        parts = table_index_str.split("_nested_")
        if len(parts) < 2:
            app_logger.error(f"Invalid nested table index format: {table_index_str}")
            return
        
        parent_table_index = safe_convert_to_int(parts[0])
        
        tables = sdt_content.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
        if parent_table_index >= len(tables):
            app_logger.error(f"Invalid parent table index: {parent_table_index} (total tables: {len(tables)})")
            return
        
        current_table = tables[parent_table_index]
        
        # Process each nesting level
        # parts[1:] contains nested path info, each in format "row_col_tableindex"
        for level_idx, nested_info in enumerate(parts[1:]):
            nested_parts = nested_info.split('_')
            
            # Each nested level should have exactly 3 parts: row, col, table_index
            if len(nested_parts) != 3:
                app_logger.error(f"Invalid nested path format at level {level_idx}: {nested_info}, expected 'row_col_tableindex'")
                return
            
            try:
                parent_row_idx = safe_convert_to_int(nested_parts[0])
                parent_col_idx = safe_convert_to_int(nested_parts[1])
                nested_table_idx = safe_convert_to_int(nested_parts[2])
            except (ValueError, IndexError) as e:
                app_logger.error(f"Error parsing nested path at level {level_idx}: {nested_info}, error: {e}")
                return
            
            rows = current_table.xpath('./w:tr', namespaces=namespaces)
            if parent_row_idx >= len(rows):
                app_logger.error(f"Nested table row index {parent_row_idx} out of bounds at level {level_idx} (total rows: {len(rows)})")
                return
            
            row = rows[parent_row_idx]
            cells = row.xpath('./w:tc', namespaces=namespaces)
            
            if parent_col_idx >= len(cells):
                app_logger.error(f"Nested table col index {parent_col_idx} out of bounds at level {level_idx} (total cols: {len(cells)})")
                return
                
            cell = cells[parent_col_idx]
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            
            if nested_table_idx >= len(nested_tables):
                app_logger.error(f"Nested table index {nested_table_idx} out of bounds at level {level_idx} (total nested tables: {len(nested_tables)})")
                return
            
            current_table = nested_tables[nested_table_idx]
            app_logger.debug(f"SDT: Navigated to nesting level {level_idx + 1}: row={parent_row_idx}, col={parent_col_idx}, table={nested_table_idx}")
        
        # Now update the cell in the final nested table
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = current_table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Final row index {row_idx} out of bounds (total rows: {len(rows)})")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Final col index {col_idx} out of bounds (total cols: {len(cells)})")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds (total paragraphs: {len(cell_paragraphs)})")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(target_paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                target_paragraph, bilingual_text, namespaces, None, field_info, original_structure
            )
        
        app_logger.info(f"Successfully updated SDT nested table cell at path: {table_index_str}")
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating SDT nested table cell: {e}, table_index: {item.get('table_index')}")

def update_paragraph_with_bilingual_format(item, bilingual_text, all_main_elements, namespaces):
    """Update paragraph with bilingual format"""
    try:
        element_index = item.get("element_index")
        if element_index is None or element_index >= len(all_main_elements):
            app_logger.error(f"Invalid element index: {element_index}")
            return
            
        paragraph = all_main_elements[element_index]
        
        if paragraph.tag.split('}')[-1] != 'p':
            app_logger.error(f"Element at index {element_index} is not a paragraph")
            return
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(paragraph, bilingual_text, namespaces, toc_structure)
        else:
            numbering_info_item = item.get("numbering_info")
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                paragraph, bilingual_text, namespaces, numbering_info_item, field_info, original_structure
            )
            
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating paragraph with index {item.get('element_index')}: {e}")

def update_table_cell_with_bilingual_format(item, bilingual_text, all_main_elements, namespaces):
    """Update table cell with bilingual format"""
    try:
        table_index = item.get("table_index")
        if isinstance(table_index, str) and "_nested_" in str(table_index):
            # Handle nested table
            update_nested_table_cell_with_bilingual_format(
                item, bilingual_text, all_main_elements, namespaces
            )
            return
        
        if table_index is None or table_index >= len(all_main_elements):
            app_logger.error(f"Invalid table index: {table_index}")
            return
        
        table = all_main_elements[table_index]
        
        if table.tag.split('}')[-1] != 'tbl':
            app_logger.error(f"Element at index {table_index} is not a table")
            return
        
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Row index {row_idx} out of bounds")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Column index {col_idx} out of bounds")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(target_paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                target_paragraph, bilingual_text, namespaces, None, field_info, original_structure
            )
            
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating table cell: {e}")

def update_nested_table_cell_with_bilingual_format(item, bilingual_text, all_main_elements, namespaces):
    """Update nested table cell with bilingual format - FIXED for multi-level nesting"""
    try:
        # Parse nested table identifier
        table_index_str = str(item.get("table_index"))
        
        # Split by "_nested_" to get each nesting level
        parts = table_index_str.split("_nested_")
        if len(parts) < 2:
            app_logger.error(f"Invalid nested table index format: {table_index_str}")
            return
        
        parent_table_index = safe_convert_to_int(parts[0])
        
        if parent_table_index >= len(all_main_elements):
            app_logger.error(f"Invalid parent table index: {parent_table_index}")
            return
        
        current_table = all_main_elements[parent_table_index]
        
        # Process each nesting level
        # parts[1:] contains nested path info, each in format "row_col_tableindex"
        for level_idx, nested_info in enumerate(parts[1:]):
            nested_parts = nested_info.split('_')
            
            # Each nested level should have exactly 3 parts: row, col, table_index
            if len(nested_parts) != 3:
                app_logger.error(f"Invalid nested path format at level {level_idx}: {nested_info}, expected 'row_col_tableindex'")
                return
            
            try:
                parent_row_idx = safe_convert_to_int(nested_parts[0])
                parent_col_idx = safe_convert_to_int(nested_parts[1])
                nested_table_idx = safe_convert_to_int(nested_parts[2])
            except (ValueError, IndexError) as e:
                app_logger.error(f"Error parsing nested path at level {level_idx}: {nested_info}, error: {e}")
                return
            
            rows = current_table.xpath('./w:tr', namespaces=namespaces)
            if parent_row_idx >= len(rows):
                app_logger.error(f"Nested table row index {parent_row_idx} out of bounds at level {level_idx} (total rows: {len(rows)})")
                return
            
            row = rows[parent_row_idx]
            cells = row.xpath('./w:tc', namespaces=namespaces)
            
            if parent_col_idx >= len(cells):
                app_logger.error(f"Nested table col index {parent_col_idx} out of bounds at level {level_idx} (total cols: {len(cells)})")
                return
                
            cell = cells[parent_col_idx]
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            
            if nested_table_idx >= len(nested_tables):
                app_logger.error(f"Nested table index {nested_table_idx} out of bounds at level {level_idx} (total nested tables: {len(nested_tables)})")
                return
            
            current_table = nested_tables[nested_table_idx]
            app_logger.debug(f"Navigated to nesting level {level_idx + 1}: row={parent_row_idx}, col={parent_col_idx}, table={nested_table_idx}")
        
        # Now update the cell in the final nested table
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = current_table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Final row index {row_idx} out of bounds (total rows: {len(rows)})")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Final col index {col_idx} out of bounds (total cols: {len(cells)})")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds (total paragraphs: {len(cell_paragraphs)})")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(target_paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                target_paragraph, bilingual_text, namespaces, None, field_info, original_structure
            )
        
        app_logger.info(f"Successfully updated nested table cell at path: {table_index_str}")
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating nested table cell: {e}, table_index: {item.get('table_index')}")

def update_textbox_with_bilingual_format(item, bilingual_text, all_wps_textboxes, all_vml_textboxes, namespaces):
    """Update textbox with bilingual format"""
    try:
        textbox_index = item.get("textbox_index")
        textbox_format = item.get("textbox_format", "wps")
        
        if textbox_format == "wps":
            if textbox_index is None or textbox_index >= len(all_wps_textboxes):
                app_logger.error(f"Invalid WPS textbox index: {textbox_index}")
                return
            textbox = all_wps_textboxes[textbox_index]
        else:  # vml
            if textbox_index is None or textbox_index >= len(all_vml_textboxes):
                app_logger.error(f"Invalid VML textbox index: {textbox_index}")
                return
            textbox = all_vml_textboxes[textbox_index]
        
        field_info = item.get("field_info")
        update_textbox_content_with_bilingual_format(textbox, bilingual_text, namespaces, field_info)
        app_logger.info(f"Updated textbox {textbox_index} with bilingual format: '{bilingual_text[:50]}...'")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating textbox: {e}")

def update_header_footer_paragraph_with_bilingual_format(item, bilingual_text, header_footer_trees, namespaces):
    """Update header/footer paragraph with bilingual format"""
    try:
        hf_file = item.get("hf_file")
        if hf_file not in header_footer_trees:
            app_logger.error(f"Header/footer file not found: {hf_file}")
            return
        
        hf_tree = header_footer_trees[hf_file]
        p_idx = item.get("paragraph_index")
        
        paragraphs = hf_tree.xpath('.//w:p[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
        if p_idx >= len(paragraphs):
            app_logger.error(f"Paragraph index {p_idx} out of bounds in {hf_file}")
            return
        
        paragraph = paragraphs[p_idx]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(paragraph, bilingual_text, namespaces, toc_structure)
        else:
            numbering_info_item = item.get("numbering_info")
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                paragraph, bilingual_text, namespaces, numbering_info_item, field_info, original_structure
            )
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating header/footer paragraph: {e}")

def update_header_footer_textbox_with_bilingual_format(item, bilingual_text, header_footer_trees, namespaces):
    """Update header/footer textbox with bilingual format"""
    try:
        hf_file = item.get("hf_file")
        if hf_file not in header_footer_trees:
            app_logger.error(f"Header/footer file not found: {hf_file}")
            return
        
        hf_tree = header_footer_trees[hf_file]
        textbox_index = item.get("textbox_index")
        textbox_format = item.get("textbox_format", "wps")
        
        if textbox_format == "wps":
            hf_textboxes = hf_tree.xpath('.//wps:txbx', namespaces=namespaces)
        else:  # vml
            hf_textboxes = hf_tree.xpath('.//v:textbox', namespaces=namespaces)
        
        if textbox_index >= len(hf_textboxes):
            app_logger.error(f"Textbox index {textbox_index} out of bounds in {hf_file}")
            return
        
        textbox = hf_textboxes[textbox_index]
        field_info = item.get("field_info")
        update_textbox_content_with_bilingual_format(textbox, bilingual_text, namespaces, field_info)
        app_logger.info(f"Updated header/footer textbox {textbox_index} with bilingual format: '{bilingual_text[:50]}...'")
        
    except (IndexError, TypeError) as e:
        app_logger.error(f"Error updating header/footer textbox: {e}")

def update_header_footer_table_cell_with_bilingual_format(item, bilingual_text, header_footer_trees, namespaces):
    """Update header/footer table cell with bilingual format"""
    try:
        hf_file = item.get("hf_file")
        if hf_file not in header_footer_trees:
            app_logger.error(f"Header/footer file not found: {hf_file}")
            return
        
        hf_tree = header_footer_trees[hf_file]
        
        # Handle nested tables in header/footer
        table_index = item.get("table_index")
        if isinstance(table_index, str) and "_nested_" in str(table_index):
            update_header_footer_nested_table_cell_with_bilingual_format(
                item, bilingual_text, hf_tree, namespaces
            )
            return
        
        tbl_idx = safe_convert_to_int(table_index) if isinstance(table_index, str) else table_index
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        tables = hf_tree.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
        if tbl_idx >= len(tables):
            app_logger.error(f"Table index {tbl_idx} out of bounds in {hf_file}")
            return
        
        table = tables[tbl_idx]
        rows = table.xpath('./w:tr', namespaces=namespaces)
        
        if row_idx >= len(rows):
            app_logger.error(f"Row index {row_idx} out of bounds in table in {hf_file}")
            return
        
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Column index {col_idx} out of bounds in table in {hf_file}")
            return
        
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds in header/footer cell")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(target_paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                target_paragraph, bilingual_text, namespaces, None, field_info, original_structure
            )
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating header/footer table cell: {e}")

def update_header_footer_nested_table_cell_with_bilingual_format(item, bilingual_text, hf_tree, namespaces):
    """Update header/footer nested table cell with bilingual format - FIXED for multi-level nesting"""
    try:
        # Parse nested table identifier
        table_index_str = str(item.get("table_index"))
        
        # Split by "_nested_" to get each nesting level
        parts = table_index_str.split("_nested_")
        if len(parts) < 2:
            app_logger.error(f"Invalid nested table index format: {table_index_str}")
            return
        
        parent_table_index = safe_convert_to_int(parts[0])
        
        tables = hf_tree.xpath('.//w:tbl[not(ancestor::wps:txbx) and not(ancestor::v:textbox) and not(ancestor::w:sdtContent)]', namespaces=namespaces)
        if parent_table_index >= len(tables):
            app_logger.error(f"Invalid parent table index: {parent_table_index} (total tables: {len(tables)})")
            return
        
        current_table = tables[parent_table_index]
        
        # Process each nesting level
        # parts[1:] contains nested path info, each in format "row_col_tableindex"
        for level_idx, nested_info in enumerate(parts[1:]):
            nested_parts = nested_info.split('_')
            
            # Each nested level should have exactly 3 parts: row, col, table_index
            if len(nested_parts) != 3:
                app_logger.error(f"Invalid nested path format at level {level_idx}: {nested_info}, expected 'row_col_tableindex'")
                return
            
            try:
                parent_row_idx = safe_convert_to_int(nested_parts[0])
                parent_col_idx = safe_convert_to_int(nested_parts[1])
                nested_table_idx = safe_convert_to_int(nested_parts[2])
            except (ValueError, IndexError) as e:
                app_logger.error(f"Error parsing nested path at level {level_idx}: {nested_info}, error: {e}")
                return
            
            rows = current_table.xpath('./w:tr', namespaces=namespaces)
            if parent_row_idx >= len(rows):
                app_logger.error(f"Nested table row index {parent_row_idx} out of bounds at level {level_idx} (total rows: {len(rows)})")
                return
            
            row = rows[parent_row_idx]
            cells = row.xpath('./w:tc', namespaces=namespaces)
            
            if parent_col_idx >= len(cells):
                app_logger.error(f"Nested table col index {parent_col_idx} out of bounds at level {level_idx} (total cols: {len(cells)})")
                return
                
            cell = cells[parent_col_idx]
            nested_tables = cell.xpath('./w:tbl', namespaces=namespaces)
            
            if nested_table_idx >= len(nested_tables):
                app_logger.error(f"Nested table index {nested_table_idx} out of bounds at level {level_idx} (total nested tables: {len(nested_tables)})")
                return
            
            current_table = nested_tables[nested_table_idx]
            app_logger.debug(f"Header/Footer: Navigated to nesting level {level_idx + 1}: row={parent_row_idx}, col={parent_col_idx}, table={nested_table_idx}")
        
        # Now update the cell in the final nested table
        row_idx = item.get("row")
        col_idx = item.get("col")
        paragraph_index = item.get("paragraph_index", 0)
        
        rows = current_table.xpath('./w:tr', namespaces=namespaces)
        if row_idx >= len(rows):
            app_logger.error(f"Final row index {row_idx} out of bounds (total rows: {len(rows)})")
            return
            
        row = rows[row_idx]
        cells = row.xpath('./w:tc', namespaces=namespaces)
        
        if col_idx >= len(cells):
            app_logger.error(f"Final col index {col_idx} out of bounds (total cols: {len(cells)})")
            return
            
        cell = cells[col_idx]
        
        # Get the specific paragraph in the cell
        cell_paragraphs = cell.xpath('./w:p', namespaces=namespaces)
        if paragraph_index >= len(cell_paragraphs):
            app_logger.error(f"Paragraph index {paragraph_index} out of bounds (total paragraphs: {len(cell_paragraphs)})")
            return
        
        target_paragraph = cell_paragraphs[paragraph_index]
        
        # Restore original paragraph properties if available
        original_pPr = item.get("original_pPr")
        if original_pPr:
            restore_paragraph_properties(target_paragraph, original_pPr, namespaces)
        
        if item.get("is_toc", False):
            toc_structure = item.get("toc_structure")
            update_toc_paragraph_with_bilingual_format(target_paragraph, bilingual_text, namespaces, toc_structure)
        else:
            field_info = item.get("field_info")
            original_structure = item.get("original_structure")
            
            update_paragraph_text_with_bilingual_format(
                target_paragraph, bilingual_text, namespaces, None, field_info, original_structure
            )
        
        app_logger.info(f"Successfully updated header/footer nested table cell at path: {table_index_str}")
        
    except (IndexError, TypeError, ValueError) as e:
        app_logger.error(f"Error updating header/footer nested table cell: {e}, table_index: {item.get('table_index')}")

def restore_paragraph_properties(paragraph, original_pPr_xml, namespaces):
    """Restore original paragraph properties from XML"""
    if not original_pPr_xml:
        return
    
    try:
        # Remove existing pPr if any
        existing_pPr = paragraph.xpath('./w:pPr', namespaces=namespaces)
        for pPr in existing_pPr:
            paragraph.remove(pPr)
        
        # Parse and insert the original pPr
        original_pPr = etree.fromstring(original_pPr_xml)
        paragraph.insert(0, original_pPr)
        
    except Exception as e:
        app_logger.error(f"Error restoring paragraph properties: {e}")

def update_paragraph_text_with_bilingual_format(paragraph, bilingual_text, namespaces, numbering_info=None, field_info=None, original_structure=None):
    """Update paragraph text with bilingual format while preserving textbox runs and other non-text elements"""
    
    # Find all direct children that are not paragraph properties
    all_children = [child for child in paragraph if not child.tag.endswith('pPr')]
    
    # Separate textbox runs from other content
    textbox_runs = []
    non_textbox_children = []
    
    for child in all_children:
        tag_name = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        
        if tag_name == 'r':  # Text run
            # Check if this run contains textbox, drawing, or other non-text content
            if (child.xpath('.//wps:txbx', namespaces=namespaces) or 
                child.xpath('.//v:textbox', namespaces=namespaces) or
                child.xpath('.//w:drawing', namespaces=namespaces) or
                child.xpath('.//w:pict', namespaces=namespaces) or
                child.xpath('.//mc:AlternateContent', namespaces=namespaces)):
                # This run contains textbox content, preserve it
                textbox_runs.append(child)
                app_logger.debug("Preserving run with textbox/drawing content")
            else:
                # This run contains only text content, treat normally
                non_textbox_children.append(child)
        else:
            # All other elements (formulas, bookmarks, etc.)
            non_textbox_children.append(child)
    
    # Remove textbox runs temporarily (they will be added back at the end)
    for run in textbox_runs:
        paragraph.remove(run)
    
    # Process non-textbox content using the original logic
    runs = []
    formulas = []
    other_elements = []
    
    for child in non_textbox_children:
        tag_name = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        
        if tag_name == 'r':  # Text run (non-textbox)
            runs.append(child)
        elif tag_name == 'oMath':  # Formula
            formulas.append(child)
        else:  # Other elements like bookmarks, etc.
            other_elements.append(child)
    
    # Remove non-textbox content except paragraph properties (original logic)
    for child in non_textbox_children:
        paragraph.remove(child)
    
    # Use original logic for non-textbox content
    # Preserve other non-text elements at the beginning
    for element in other_elements:
        if element.tag.endswith('bookmarkStart') or element.tag.endswith('proofErr'):
            paragraph.append(element)
    
    # Process bilingual text with formulas and fields (original logic)
    if field_info:
        # Extract formulas from field_info for the update function
        formulas_from_field = []
        for item in field_info:
            if item.get('type') == 'formula' and item.get('xml_content'):
                try:
                    formula_element = etree.fromstring(item['xml_content'])
                    formulas_from_field.append(formula_element)
                except Exception as e:
                    app_logger.warning(f"Error parsing formula XML: {e}")
        
        update_paragraph_content_with_fields_and_formulas_bilingual(
            paragraph, bilingual_text, namespaces, field_info, formulas, original_structure
        )
    else:
        add_text_with_formulas_and_bilingual_formatting(
            paragraph, bilingual_text, namespaces, formulas, original_structure
        )
    
    # Add remaining other elements at the end (original logic)
    for element in other_elements:
        if not (element.tag.endswith('bookmarkStart') or element.tag.endswith('proofErr')):
            paragraph.append(element)
    
    # Add back textbox runs at the end
    for run in textbox_runs:
        paragraph.append(run)
    
    app_logger.debug(f"Updated paragraph preserving {len(textbox_runs)} textbox runs")

def apply_latin_font_to_run(run, namespaces, target_language=None):
    """为非中文目标语言的文本运行设置罗马字体"""
    # 使用全局目标语言或传入的目标语言
    effective_target = target_language or globals().get('_current_target_language') or DateConversionConfig.TARGET_LANGUAGE
    
    # 如果目标语言是中文相关，不设置罗马字体
    if effective_target and effective_target.lower() in ['zh', 'zh-cn', 'zh-tw', 'chinese']:
        return
    
    # 查找或创建运行属性
    rPr = run.xpath('./w:rPr', namespaces=namespaces)
    if not rPr:
        rPr_element = etree.SubElement(run, f"{{{namespaces['w']}}}rPr")
    else:
        rPr_element = rPr[0]
    
    # 设置罗马字体
    # 查找现有的字体设置
    existing_fonts = rPr_element.xpath('./w:rFonts', namespaces=namespaces)
    if existing_fonts:
        rFonts = existing_fonts[0]
    else:
        rFonts = etree.SubElement(rPr_element, f"{{{namespaces['w']}}}rFonts")
    
    # 为非中文内容设置罗马字体
    font_name = "Times New Roman"  # 可以根据需要修改字体
    rFonts.set(f'{{{namespaces["w"]}}}ascii', font_name)
    rFonts.set(f'{{{namespaces["w"]}}}hAnsi', font_name)
    rFonts.set(f'{{{namespaces["w"]}}}cs', font_name)

def update_paragraph_content_with_fields_and_formulas_bilingual(paragraph, bilingual_text, namespaces, field_info, formulas, original_structure):
    """Update paragraph content with fields and formulas while maintaining bilingual format"""
    
    # Extract formula info and field info
    formula_items = [item for item in field_info if item.get('type') == 'formula']
    field_items = [item for item in field_info if item.get('type') != 'formula']
    footnote_ref_items = [item for item in field_items if item.get('type') == 'footnote_reference']
    other_field_items = [item for item in field_items if item.get('type') != 'footnote_reference']
    
    # Create formula mapping
    formula_mapping = {}
    for formula_item in formula_items:
        placeholder = formula_item.get('placeholder', '')
        if placeholder:
            formula_mapping[placeholder] = formula_item
    
    # Create footnote reference mapping
    footnote_ref_mapping = {}
    for footnote_item in footnote_ref_items:
        placeholder = footnote_item.get('placeholder', '')
        if placeholder:
            footnote_ref_mapping[placeholder] = footnote_item
    
    # Parse the bilingual text to find placeholders
    field_placeholders = re.findall(r'\{\{[^}]+\}\}', bilingual_text)
    formula_placeholders = re.findall(r'\[formula_\d+\]', bilingual_text)
    footnote_ref_placeholders = re.findall(r'\{\{FOOTNOTE_REF_\d+\}\}', bilingual_text)
    
    # Create a mapping of field placeholders to field info
    field_mapping = {}
    field_counters = {}
    
    for field in other_field_items:
        display_text = field.get('display_text', '')
        if display_text in field_placeholders:
            if display_text not in field_counters:
                field_counters[display_text] = 0
            
            key = f"{display_text}_{field_counters[display_text]}"
            field_mapping[key] = field
            field_counters[display_text] += 1
    
    # Split text by both field placeholders and line breaks
    lines = bilingual_text.split('\n')
    
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            # Add line break before each line except the first
            br_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
            apply_latin_font_to_run(br_run, namespaces)
            etree.SubElement(br_run, f"{{{namespaces['w']}}}br")
        
        # Process all placeholders within this line
        all_placeholders = field_placeholders + formula_placeholders + footnote_ref_placeholders
        if any(placeholder in line for placeholder in all_placeholders):
            # Split line by all placeholders and rebuild with proper elements
            pattern = r'(\{\{[^}]+\}\}|\[formula_\d+\])'
            parts = re.split(pattern, line)
            
            current_run = None
            field_usage = {}
            
            for part in parts:
                if part in formula_placeholders:
                    # This is a formula placeholder
                    if part in formula_mapping:
                        formula_item = formula_mapping[part]
                        try:
                            # Parse and insert the original formula XML
                            formula_xml = formula_item.get('xml_content', '')
                            if formula_xml:
                                formula_element = etree.fromstring(formula_xml)
                                paragraph.append(formula_element)
                                app_logger.debug(f"Inserted formula: {part}")
                        except Exception as e:
                            app_logger.error(f"Error inserting formula {part}: {e}")
                            # Fallback: create text with placeholder
                            if current_run is None:
                                current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                                apply_latin_font_to_run(current_run, namespaces)
                            text_node = etree.SubElement(current_run, f"{{{namespaces['w']}}}t")
                            text_node.text = part
                        
                        current_run = None  # Force new run for next content
                
                elif part in footnote_ref_placeholders:
                    # This is a footnote reference placeholder
                    if part in footnote_ref_mapping:
                        footnote_item = footnote_ref_mapping[part]
                        try:
                            # Parse and insert the original footnote reference run XML
                            run_xml = footnote_item.get('run_xml', '')
                            if run_xml:
                                footnote_run = etree.fromstring(run_xml)
                                paragraph.append(footnote_run)
                                app_logger.debug(f"Inserted footnote reference: {part}")
                        except Exception as e:
                            app_logger.error(f"Error inserting footnote reference {part}: {e}")
                            # Fallback: create text with placeholder
                            if current_run is None:
                                current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                                apply_latin_font_to_run(current_run, namespaces)
                            text_node = etree.SubElement(current_run, f"{{{namespaces['w']}}}t")
                            text_node.text = part
                        
                        current_run = None  # Force new run for next content
                    
                elif part in field_placeholders:
                    # This is a field placeholder (not footnote reference)
                    if part not in field_usage:
                        field_usage[part] = 0
                    
                    key = f"{part}_{field_usage[part]}"
                    field_usage[part] += 1
                    
                    if key in field_mapping:
                        field = field_mapping[key]
                        field_type = field.get('type')
                        
                        if current_run is None:
                            current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                            apply_latin_font_to_run(current_run, namespaces)
                        
                        if field_type == 'simple_field':
                            # Create simple field using original XML
                            original_field_xml = field.get('original_field_xml')
                            if original_field_xml:
                                try:
                                    field_element = etree.fromstring(original_field_xml)
                                    current_run.append(field_element)
                                except:
                                    # Fallback to manual creation
                                    fld_simple = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldSimple")
                                    fld_simple.set(f'{{{namespaces["w"]}}}instr', field.get('instruction', ''))
                            else:
                                # Manual creation
                                fld_simple = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldSimple")
                                fld_simple.set(f'{{{namespaces["w"]}}}instr', field.get('instruction', ''))
                        
                        # Handle other field types (begin, end, separate, instruction)
                        elif field_type == 'field_begin':
                            fld_char = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldChar")
                            fld_char.set(f'{{{namespaces["w"]}}}fldCharType', 'begin')
                        elif field_type == 'field_instruction':
                            instr_text = etree.SubElement(current_run, f"{{{namespaces['w']}}}instrText")
                            instr_text.text = field.get('instruction', '')
                        elif field_type == 'field_end':
                            fld_char = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldChar")
                            fld_char.set(f'{{{namespaces["w"]}}}fldCharType', 'end')
                        elif field_type == 'field_separate':
                            fld_char = etree.SubElement(current_run, f"{{{namespaces['w']}}}fldChar")
                            fld_char.set(f'{{{namespaces["w"]}}}fldCharType', 'separate')
                        
                        current_run = None  # Force new run for next content
                    
                elif part.strip() or part == '':
                    # This is regular text (including empty strings to preserve structure)
                    if current_run is None:
                        current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                        apply_latin_font_to_run(current_run, namespaces)
                    
                    text_node = etree.SubElement(current_run, f"{{{namespaces['w']}}}t")
                    text_node.text = part
        else:
            # No placeholders in this line, just add as regular text
            if line or line_idx == 0:  # Always add first line, add others only if not empty
                text_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                apply_latin_font_to_run(text_run, namespaces)
                text_node = etree.SubElement(text_run, f"{{{namespaces['w']}}}t")
                text_node.text = line

def add_text_with_formulas_and_bilingual_formatting(paragraph, bilingual_text, namespaces, formulas, original_structure):
    """Add bilingual text to paragraph with formula support while preserving line breaks and formatting"""
    
    # Extract formula placeholders and footnote reference placeholders from text
    formula_placeholders = re.findall(r'\[formula_\d+\]', bilingual_text)
    footnote_ref_placeholders = re.findall(r'\{\{FOOTNOTE_REF_\d+\}\}', bilingual_text)
    
    # Create formula mapping from the formulas list
    formula_mapping = {}
    for i, formula in enumerate(formulas):
        placeholder = f"[formula_{i + 1}]"
        formula_mapping[placeholder] = formula
    
    # Split text by line breaks
    lines = bilingual_text.split('\n')
    
    for line_idx, line in enumerate(lines):
        if line_idx > 0:
            # Add line break before each line except the first
            br_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
            apply_latin_font_to_run(br_run, namespaces)
            etree.SubElement(br_run, f"{{{namespaces['w']}}}br")
        
        # Process placeholders within this line
        all_placeholders = formula_placeholders + footnote_ref_placeholders
        if any(placeholder in line for placeholder in all_placeholders):
            # Split line by formula placeholders and rebuild with proper elements
            parts = re.split(r'(\[formula_\d+\]|\{\{FOOTNOTE_REF_\d+\}\})', line)
            
            current_run = None
            
            for part in parts:
                if part in formula_placeholders:
                    # This is a formula placeholder
                    if part in formula_mapping:
                        # Insert the original formula element
                        paragraph.append(formula_mapping[part])
                        app_logger.debug(f"Inserted formula: {part}")
                    else:
                        # Fallback: create text with placeholder if formula not found
                        if current_run is None:
                            current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                            apply_latin_font_to_run(current_run, namespaces)
                        text_node = etree.SubElement(current_run, f"{{{namespaces['w']}}}t")
                        text_node.text = part
                    
                    current_run = None  # Force new run for next content
                
                elif part in footnote_ref_placeholders:
                    # This is a footnote reference placeholder, but we don't have the info to restore it
                    # Leave as placeholder for now - it should be handled by the field_info processing
                    if current_run is None:
                        current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                        apply_latin_font_to_run(current_run, namespaces)
                    
                    text_node = etree.SubElement(current_run, f"{{{namespaces['w']}}}t")
                    text_node.text = part
                
                elif part.strip() or part == '':
                    # This is regular text (including empty strings to preserve structure)
                    if current_run is None:
                        current_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                        apply_latin_font_to_run(current_run, namespaces)
                    
                    text_node = etree.SubElement(current_run, f"{{{namespaces['w']}}}t")
                    text_node.text = part
        else:
            # No placeholders in this line, just add as regular text
            if line or line_idx == 0:  # Always add first line, add others only if not empty
                text_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                apply_latin_font_to_run(text_run, namespaces)
                text_node = etree.SubElement(text_run, f"{{{namespaces['w']}}}t")
                text_node.text = line

def update_textbox_content_with_bilingual_format(textbox, bilingual_text, namespaces, field_info=None):
    """Update textbox content with bilingual format and formula support"""
    textbox_content = textbox.xpath('.//w:txbxContent', namespaces=namespaces)
    if not textbox_content:
        app_logger.error("No textbox content found")
        return
    
    # Get original formatting from existing paragraphs before clearing
    original_formatting = None
    existing_paragraphs = textbox_content[0].xpath('./w:p', namespaces=namespaces)
    if existing_paragraphs:
        first_p = existing_paragraphs[0]
        first_runs = first_p.xpath('./w:r', namespaces=namespaces)
        if first_runs:
            rPr_elements = first_runs[0].xpath('./w:rPr', namespaces=namespaces)
            if rPr_elements:
                original_formatting = rPr_elements[0]
    
    # Clear all existing paragraphs
    for child in list(textbox_content[0]):
        textbox_content[0].remove(child)
    
    # Handle multi-line text (including bilingual format)
    if "\n" in bilingual_text:
        text_lines = bilingual_text.split("\n")
        for line_idx, line in enumerate(text_lines):
            # Create new paragraph
            new_p = etree.SubElement(textbox_content[0], f"{{{namespaces['w']}}}p")
            
            # Process line with field variables and formulas
            if field_info:
                # Extract formulas from field_info
                formulas = [etree.fromstring(item['xml_content']) for item in field_info if item.get('type') == 'formula' and item.get('xml_content')]
                update_paragraph_content_with_fields_and_formulas_bilingual(
                    new_p, line, namespaces, field_info, formulas, None
                )
            else:
                add_text_with_formulas_and_bilingual_formatting(new_p, line, namespaces, [], None)
    else:
        # Single line text - create one paragraph
        new_p = etree.SubElement(textbox_content[0], f"{{{namespaces['w']}}}p")
        
        # Process text with field variables and formulas
        if field_info:
            # Extract formulas from field_info
            formulas = [etree.fromstring(item['xml_content']) for item in field_info if item.get('type') == 'formula' and item.get('xml_content')]
            update_paragraph_content_with_fields_and_formulas_bilingual(
                new_p, bilingual_text, namespaces, field_info, formulas, None
            )
        else:
            add_text_with_formulas_and_bilingual_formatting(new_p, bilingual_text, namespaces, [], None)

def update_toc_paragraph_with_bilingual_format(paragraph, bilingual_text, namespaces, toc_structure):
    """Update TOC paragraph with bilingual format (original + translation)"""
    
    # For TOC entries, we need to be careful to preserve the structure
    # but still show both original and translated title
    
    # Extract original and translated parts from bilingual text
    lines = bilingual_text.split('\n')
    if len(lines) >= 2:
        original_title = lines[0]
        translated_title = lines[1]
    else:
        # Fallback if format is not as expected
        original_title = bilingual_text
        translated_title = bilingual_text
    
    if not toc_structure or not toc_structure.get('run_details'):
        app_logger.warning("No TOC structure available, using fallback for bilingual TOC")
        update_toc_paragraph_bilingual_fallback(paragraph, original_title, translated_title, namespaces)
        return
    
    run_details = toc_structure['run_details']
    total_runs = toc_structure.get('total_runs', 0)
    
    # Get all current runs in the paragraph
    current_runs = paragraph.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
    
    if len(current_runs) != total_runs:
        app_logger.warning(f"Run count mismatch for TOC: expected {total_runs}, found {len(current_runs)}")
        update_toc_paragraph_bilingual_fallback(paragraph, original_title, translated_title, namespaces)
        return
    
    # Update only the title runs with the bilingual text
    title_run_indices = [detail['index'] for detail in run_details if detail['type'] == 'title']
    
    if not title_run_indices:
        app_logger.warning("No title runs found in TOC structure")
        update_toc_paragraph_bilingual_fallback(paragraph, original_title, translated_title, namespaces)
        return
    
    try:
        # Clear text content from title runs
        for run_idx in title_run_indices:
            if run_idx < len(current_runs):
                run = current_runs[run_idx]
                # Remove all text nodes
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    text_node.getparent().remove(text_node)
        
        # Add the bilingual text to the first title run
        first_title_run_idx = title_run_indices[0]
        if first_title_run_idx < len(current_runs):
            first_title_run = current_runs[first_title_run_idx]
            
            # Apply font settings to the run
            apply_latin_font_to_run(first_title_run, namespaces)
            
            # Create text node with original title
            original_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
            original_text_node.text = original_title
            
            # Add line break
            etree.SubElement(first_title_run, f"{{{namespaces['w']}}}br")
            
            # Create text node with translated title
            translated_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
            translated_text_node.text = translated_title
            
            app_logger.info(f"Successfully updated TOC paragraph with bilingual format: '{original_title}' / '{translated_title}'")
    
    except Exception as e:
        app_logger.error(f"Error updating TOC with bilingual format: {e}")
        update_toc_paragraph_bilingual_fallback(paragraph, original_title, translated_title, namespaces)

def update_toc_paragraph_bilingual_fallback(paragraph, original_title, translated_title, namespaces):
    """Fallback method for updating TOC paragraph with bilingual format"""
    try:
        # Check if paragraph is in a hyperlink
        hyperlinks = paragraph.xpath('.//w:hyperlink', namespaces=namespaces)
        
        if hyperlinks:
            # Process hyperlink-based TOC
            hyperlink = hyperlinks[0]
            hyperlink_runs = hyperlink.xpath('.//w:r[not(ancestor::wps:txbx) and not(ancestor::v:textbox)]', namespaces=namespaces)
            
            title_runs = []
            
            # Identify title runs and preserve non-title elements
            for run in hyperlink_runs:
                run_text = ""
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    run_text += text_node.text or ""
                
                # Check if this run contains tabs, dots, or page numbers
                if (run.xpath('.//w:tab', namespaces=namespaces) or
                    is_dot_leader(run_text) or
                    is_likely_page_number(run_text) or
                    run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces)):
                    # This is a non-title element, preserve it
                    continue
                else:
                    # This is likely a title run
                    if run_text.strip() and not re.match(r'^\d+\.?$', run_text.strip()):
                        title_runs.append(run)
            
            # Clear text from title runs only
            for run in title_runs:
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    text_node.getparent().remove(text_node)
            
            # Add bilingual text to the first title run if available
            if title_runs:
                first_title_run = title_runs[0]
                
                # Apply font settings
                apply_latin_font_to_run(first_title_run, namespaces)
                
                # Add original title
                original_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
                original_text_node.text = original_title
                
                # Add line break
                etree.SubElement(first_title_run, f"{{{namespaces['w']}}}br")
                
                # Add translated title
                translated_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
                translated_text_node.text = translated_title
            else:
                # Create a new run for the bilingual title if no title runs found
                new_run = etree.Element(f"{{{namespaces['w']}}}r")
                
                # Apply font settings
                apply_latin_font_to_run(new_run, namespaces)
                
                # Add original title
                original_text_node = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                original_text_node.text = original_title
                
                # Add line break
                etree.SubElement(new_run, f"{{{namespaces['w']}}}br")
                
                # Add translated title
                translated_text_node = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                translated_text_node.text = translated_title
                
                hyperlink.insert(0, new_run)
        
        else:
            # Process non-hyperlink TOC paragraph
            all_runs = paragraph.xpath('./w:r', namespaces=namespaces)
            
            title_runs = []
            formatting = None
            
            # Identify runs that contain title text (not tabs, dots, or page numbers)
            for run in all_runs:
                run_text = ""
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    run_text += text_node.text or ""
                
                # Skip runs with tabs, dots, page numbers, or fields
                if (run.xpath('.//w:tab', namespaces=namespaces) or
                    is_dot_leader(run_text) or
                    is_likely_page_number(run_text) or
                    run.xpath('.//w:fldChar | .//w:instrText | .//w:fldSimple', namespaces=namespaces)):
                    continue
                
                # This is likely a title run
                if run_text.strip() and not re.match(r'^\d+\.?$', run_text.strip()):
                    title_runs.append(run)
                    # Get formatting from the first title run
                    if formatting is None:
                        rPr_elements = run.xpath('./w:rPr', namespaces=namespaces)
                        if rPr_elements:
                            formatting = rPr_elements[0]
            
            # Clear text from title runs
            for run in title_runs:
                text_nodes = run.xpath('.//w:t', namespaces=namespaces)
                for text_node in text_nodes:
                    text_node.getparent().remove(text_node)
            
            # Add bilingual text
            if title_runs:
                first_title_run = title_runs[0]
                
                # Apply font settings
                apply_latin_font_to_run(first_title_run, namespaces)
                
                # Add original title
                original_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
                original_text_node.text = original_title
                
                # Add line break
                etree.SubElement(first_title_run, f"{{{namespaces['w']}}}br")
                
                # Add translated title
                translated_text_node = etree.SubElement(first_title_run, f"{{{namespaces['w']}}}t")
                translated_text_node.text = translated_title
            else:
                # Create new run for bilingual title if none found
                new_run = etree.SubElement(paragraph, f"{{{namespaces['w']}}}r")
                if formatting is not None:
                    cloned_rPr = etree.fromstring(etree.tostring(formatting))
                    new_run.insert(0, cloned_rPr)
                
                # Apply font settings
                apply_latin_font_to_run(new_run, namespaces)
                
                # Add original title
                original_text_node = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                original_text_node.text = original_title
                
                # Add line break
                etree.SubElement(new_run, f"{{{namespaces['w']}}}br")
                
                # Add translated title
                translated_text_node = etree.SubElement(new_run, f"{{{namespaces['w']}}}t")
                translated_text_node.text = translated_title
        
        app_logger.info(f"Updated TOC paragraph (bilingual fallback) with: '{original_title}' / '{translated_title}'")
    
    except Exception as e:
        app_logger.error(f"Error in TOC bilingual fallback update: {e}")
        # Last resort: simple bilingual text replacement
        update_paragraph_text_with_bilingual_format(paragraph, f"{original_title}\n{translated_title}", namespaces, None, None, None)

def update_numbering_xml_with_translations(numbering_tree, original_data, translations, namespaces):
    """Update numbering.xml with translated content, preserving variable placeholders"""
    for item in original_data:
        if item["type"] == "numbering_level_text":
            item_id = str(item.get("id", item.get("count_src")))
            translated_text = translations.get(item_id)
            
            if translated_text:
                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                
                # Find the corresponding lvlText element using xpath
                element_xpath = item.get("element_xpath")
                if element_xpath:
                    try:
                        lvl_text_nodes = numbering_tree.xpath(element_xpath, namespaces=namespaces)
                        for lvl_text_node in lvl_text_nodes:
                            # Validate that this is the correct node by checking original value
                            current_val = lvl_text_node.get(f'{{{namespaces["w"]}}}val', '')
                            original_val = item.get("original_lvl_text", "")
                            
                            if current_val == original_val:
                                # Extract and preserve variable placeholders from translated text
                                final_translated_text = extract_and_preserve_variables(translated_text, original_val)
                                
                                # Update the lvlText value
                                lvl_text_node.set(f'{{{namespaces["w"]}}}val', final_translated_text)
                                app_logger.info(f"Updated numbering level text: '{original_val}' -> '{final_translated_text}'")
                                break
                    except Exception as e:
                        app_logger.error(f"Error updating numbering level text: {e}")
        
        elif item["type"] == "numbering_text_node":
            item_id = str(item.get("id", item.get("count_src")))
            translated_text = translations.get(item_id)
            
            if translated_text:
                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                
                # Find the text node using the original text value
                original_text = item.get("original_text", "")
                text_nodes = numbering_tree.xpath('.//w:t', namespaces=namespaces)
                
                for text_node in text_nodes:
                    if text_node.text and text_node.text.strip() == original_text:
                        text_node.text = translated_text
                        app_logger.info(f"Updated numbering text node: '{original_text}' -> '{translated_text}'")
                        break

def extract_and_preserve_variables(translated_text, original_text):
    """Extract variable placeholders from translated text and ensure they're preserved correctly"""
    # Find all variables in the original text
    original_variables = re.findall(r'%\d+', original_text)
    
    # Find all variables in the translated text
    translated_variables = re.findall(r'%\d+', translated_text)
    
    # If all variables are preserved in translation, return as is
    if set(original_variables) == set(translated_variables):
        return translated_text
    
    # If some variables are missing, try to restore them
    if original_variables:
        # Check if the translation removed variables
        if not translated_variables:
            # Variables were removed, try to restore them by finding the best position
            app_logger.warning(f"Variables {original_variables} were removed from translation, attempting to restore")
            
            # For now, just append the first variable to the end
            if original_variables:
                return translated_text + original_variables[0]
        else:
            # Some variables preserved, ensure all are present
            missing_vars = set(original_variables) - set(translated_variables)
            if missing_vars:
                app_logger.warning(f"Missing variables {missing_vars} in translation")
                # Add missing variables at the end
                for var in missing_vars:
                    translated_text += var
    
    return translated_text

def update_json_structure_after_translation(original_json_path, translated_json_path):
    """Update JSON structure after translation"""
    with open(original_json_path, "r", encoding="utf-8") as orig_file:
        original_data = json.load(orig_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as trans_file:
        translated_data = json.load(trans_file)
    
    translations_by_id = {}
    for item in translated_data:
        if "translated" in item:
            item_id = str(item.get("id", item.get("count_src")))
            if item_id:
                translations_by_id[item_id] = item["translated"]
    
    restructured_data = []
    for item in original_data:
        item_id = str(item.get("id", item.get("count_src")))
        if item_id in translations_by_id:
            new_item = {
                "id": item.get("id"),
                "count_src": item.get("count_src"),
                "type": item["type"],
                "translated": translations_by_id[item_id]
            }
            
            # Preserve important metadata including TOC structure and all format info
            preserve_keys = [
                "is_heading", "has_numbering", "numbering_info", "is_toc", "toc_info", "toc_structure", 
                "textbox_type", "textbox_format", "textbox_index", "positioning_info", "paragraph_context",
                "field_info", "original_pPr", "original_structure", "table_props", "row_props", "cell_props",
                "paragraph_index", "nesting_level", "sdt_index", "is_toc_sdt", "sdt_props", "value",
                # SmartArt specific keys
                "diagram_index", "shape_index", "tx_body_index", "model_id", "run_texts", "run_styles", 
                "run_lengths", "drawing_path", "original_text", "xpath"
            ]
            
            for key in preserve_keys:
                if key in item:
                    new_item[key] = item[key]
            
            restructured_data.append(new_item)
    
    with open(translated_json_path, "w", encoding="utf-8") as outfile:
        json.dump(restructured_data, outfile, ensure_ascii=False, indent=4)
    
    app_logger.info(f"Updated translation JSON structure: {translated_json_path}")
    return translated_json_path
