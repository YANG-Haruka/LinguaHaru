# pipeline/excel_translation_pipeline_test.py  By AI-Transtools
import os
import json
import re
import tempfile
import shutil
from datetime import datetime
from zipfile import ZipFile
from lxml import etree
from typing import Dict, List, Any, Tuple, Set
import xlwings as xw
from .skip_pipeline import should_translate
from config.log_config import app_logger


def extract_excel_content_to_json(file_path,temp_dir):
    cell_data = []
    count = 0
    
    app = xw.App(visible=False)
    app.screen_updating = False
    
    try:
        wb = app.books.open(file_path)

        # First, collect sheet names
        sheets = list(wb.sheets)
        for sheet in sheets:
            sheet_name = sheet.name
            if should_translate(sheet_name):
                count += 1
                cell_data.append({
                    "count_src": count,
                    "sheet": sheet_name,
                    "value": sheet_name,
                    "type": "sheet_name"
                })
        
        def get_merged_ranges(sheet):
            """获取工作表中所有合并单元格范围"""
            merged_ranges = []
            try:
                # 尝试获取合并单元格信息
                merge_areas = sheet.api.Cells.MergeArea
                if merge_areas:
                    # 如果只有一个合并区域
                    if hasattr(merge_areas, 'Address'):
                        merged_ranges.append(merge_areas.Address)
                    else:
                        # 如果有多个合并区域，需要遍历
                        for area in merge_areas:
                            if hasattr(area, 'Address'):
                                merged_ranges.append(area.Address)
            except:
                # 如果上述方法失败，使用替代方法
                try:
                    used_range = sheet.used_range
                    if used_range:
                        max_row = used_range.last_cell.row
                        max_col = used_range.last_cell.column
                        
                        # 检查每个单元格是否为合并单元格的左上角
                        for row in range(1, min(max_row + 1, 1000)):  # 限制检查范围以提高性能
                            for col in range(1, min(max_col + 1, 100)):
                                try:
                                    cell = sheet.cells(row, col)
                                    if cell.api.MergeCells:
                                        merge_area = cell.api.MergeArea
                                        if (merge_area.Row == row and merge_area.Column == col):
                                            # 这是合并区域的左上角单元格
                                            address = merge_area.Address
                                            if address not in merged_ranges:
                                                merged_ranges.append(address)
                                except:
                                    continue
                except Exception as e:
                    app_logger.warning(f"Failed to get merged ranges for sheet {sheet.name}: {str(e)}")
            
            app_logger.info(f"Found {len(merged_ranges)} merged ranges in sheet '{sheet.name}'")
            return merged_ranges

        def parse_range_address(address):
            """解析Excel地址范围，返回(start_row, start_col, end_row, end_col)"""
            try:
                # 移除工作表名前缀（如果存在）
                if '!' in address:
                    address = address.split('!')[-1]
                
                # 移除 $ 符号
                address = address.replace('$', '')
                
                if ':' in address:
                    start_addr, end_addr = address.split(':')
                else:
                    start_addr = end_addr = address
                
                def addr_to_row_col(addr):
                    # 分离字母和数字
                    col_str = ''.join([c for c in addr if c.isalpha()])
                    row_str = ''.join([c for c in addr if c.isdigit()])
                    
                    # 将列字母转换为列号
                    col_num = 0
                    for c in col_str:
                        col_num = col_num * 26 + (ord(c.upper()) - ord('A') + 1)
                    
                    return int(row_str), col_num
                
                start_row, start_col = addr_to_row_col(start_addr)
                end_row, end_col = addr_to_row_col(end_addr)
                
                return start_row, start_col, end_row, end_col
            except Exception as e:
                app_logger.warning(f"Failed to parse range address '{address}': {str(e)}")
                return None

        def is_cell_in_merged_ranges(row, col, merged_ranges, parsed_ranges_cache):
            """检查单元格是否在合并区域中，以及是否为合并区域的左上角"""
            for i, range_addr in enumerate(merged_ranges):
                if range_addr not in parsed_ranges_cache:
                    parsed = parse_range_address(range_addr)
                    if parsed:
                        parsed_ranges_cache[range_addr] = parsed
                    else:
                        continue
                
                start_row, start_col, end_row, end_col = parsed_ranges_cache[range_addr]
                
                # 检查单元格是否在这个合并区域内
                if start_row <= row <= end_row and start_col <= col <= end_col:
                    # 返回是否为左上角单元格
                    is_top_left = (row == start_row and col == start_col)
                    return True, is_top_left, (start_row, start_col, end_row, end_col)
            
            return False, False, None

        def process_sheet(sheet):
            nonlocal count
            sheet_data = []
            
            # 获取合并单元格信息
            merged_ranges = get_merged_ranges(sheet)
            parsed_ranges_cache = {}
            
            # Process cells - improved logic
            try:
                used_range = sheet.used_range
                if used_range:
                    app_logger.info(f"Processing sheet '{sheet.name}' with range: {used_range.address}")
                    
                    # Get all values at once for better performance
                    max_row = used_range.last_cell.row
                    max_col = used_range.last_cell.column
                    
                    app_logger.info(f"Sheet '{sheet.name}' dimensions: {max_row} rows × {max_col} columns")
                    
                    # 分批处理大量数据
                    batch_size = 5000  # 每次处理5000行
                    processed_cells = 0
                    
                    for batch_start in range(1, max_row + 1, batch_size):
                        batch_end = min(batch_start + batch_size - 1, max_row)
                        app_logger.info(f"Processing rows {batch_start} to {batch_end}")
                        
                        # 对于每一行，逐列处理
                        for row_idx in range(batch_start, batch_end + 1):
                            for col_idx in range(1, max_col + 1):
                                try:
                                    cell = sheet.cells(row_idx, col_idx)
                                    cell_value = cell.value
                                    
                                    # Skip None values
                                    if cell_value is None:
                                        continue
                                    
                                    # Skip datetime objects
                                    if isinstance(cell_value, datetime):
                                        continue
                                    
                                    # Convert to string for processing
                                    cell_value_str = str(cell_value)
                                    
                                    # Skip formula cells (cells that start with '=')
                                    if cell_value_str.strip().startswith('='):
                                        continue
                                    
                                    # Skip cells that shouldn't be translated
                                    if not should_translate(cell_value_str):
                                        continue
                                    
                                    # Check if cell is in merged range
                                    is_merged, is_top_left, merge_info = is_cell_in_merged_ranges(
                                        row_idx, col_idx, merged_ranges, parsed_ranges_cache
                                    )
                                    
                                    # Only process non-merged cells or top-left cell of merged areas
                                    if is_merged and not is_top_left:
                                        continue
                                    
                                    # Process valid cell value - 在这里立即分配count_src
                                    processed_value = cell_value_str.replace("\n", "␊").replace("\r", "␍")
                                    
                                    count += 1  # 立即递增count
                                    cell_info = {
                                        "count_src": count,  # 立即分配count_src
                                        "sheet": sheet.name,
                                        "row": row_idx,
                                        "column": col_idx,
                                        "value": processed_value,
                                        "original_value": processed_value,  # 保存原始值用于验证
                                        "is_merged": is_merged,
                                        "type": "cell"
                                    }
                                    
                                    # Add merge information if cell is merged
                                    if is_merged and merge_info:
                                        cell_info["merge_start_row"] = merge_info[0]
                                        cell_info["merge_start_col"] = merge_info[1]
                                        cell_info["merge_end_row"] = merge_info[2]
                                        cell_info["merge_end_col"] = merge_info[3]
                                    
                                    sheet_data.append(cell_info)
                                    processed_cells += 1
                                    
                                    # 每处理1000个单元格记录一次日志
                                    if processed_cells % 1000 == 0:
                                        app_logger.info(f"Processed {processed_cells} cells in sheet '{sheet.name}'")
                                    
                                except Exception as cell_error:
                                    app_logger.warning(f"Error processing cell ({row_idx}, {col_idx}): {str(cell_error)}")
                                    continue
                    
                    app_logger.info(f"Extracted {len(sheet_data)} cells from sheet '{sheet.name}'")
                    
            except Exception as e:
                app_logger.error(f"Error processing cells in sheet {sheet.name}: {str(e)}")
            
            # Process shapes - with improved error handling
            try:
                shapes = list(sheet.shapes)
                if shapes:
                    app_logger.info(f"Processing {len(shapes)} shapes in sheet '{sheet.name}'")
                    shape_name_count = {}
                    
                    # Recursive function to handle nested groups
                    def process_group_items(group, group_index, group_name, path=""):
                        group_items_data = []
                        
                        try:
                            if hasattr(group.api, 'GroupItems'):
                                group_items = group.api.GroupItems
                                for i in range(1, group_items.Count + 1):
                                    try:
                                        child_item = group_items.Item(i)
                                        item_path = f"{path}/{i}" if path else str(i)
                                        
                                        # Check if child is a group
                                        is_child_group = False
                                        try:
                                            if hasattr(child_item, 'Type') and child_item.Type == 6:  # 6 is Excel's group type
                                                is_child_group = True
                                        except:
                                            pass
                                        
                                        if is_child_group:
                                            # Process nested group recursively
                                            try:
                                                child_name = f"{group_name}_child{i}"
                                                nested_items = process_group_items(child_item, -1, child_name, item_path)
                                                group_items_data.extend(nested_items)
                                            except Exception as nested_error:
                                                app_logger.warning(f"Error processing nested group {item_path}: {str(nested_error)}")
                                        else:
                                            # Process normal shape
                                            has_text = False
                                            text_value = None
                                            
                                            # Try TextFrame
                                            try:
                                                if hasattr(child_item, 'TextFrame') and child_item.TextFrame.HasText:
                                                    text_value = child_item.TextFrame.Characters().Text
                                                    has_text = True
                                            except:
                                                pass
                                            
                                            # Try TextFrame2
                                            if not has_text:
                                                try:
                                                    if hasattr(child_item, 'TextFrame2') and child_item.TextFrame2.HasText:
                                                        text_value = child_item.TextFrame2.TextRange.Text
                                                        has_text = True
                                                except:
                                                    pass
                                            
                                            # Skip formula text in shapes
                                            if has_text and text_value and isinstance(text_value, str) and text_value.strip().startswith('='):
                                                continue
                                                
                                            # If has text and needs translation
                                            if has_text and text_value and should_translate(text_value):
                                                text_value = str(text_value).replace("\n", "␊").replace("\r", "␍")
                                                
                                                # Create unique identifier
                                                try:
                                                    child_name = child_item.Name
                                                except:
                                                    child_name = f"GroupChild_{group_name}_{item_path}"
                                                
                                                if child_name in shape_name_count:
                                                    shape_name_count[child_name] += 1
                                                else:
                                                    shape_name_count[child_name] = 1
                                                    
                                                unique_shape_id = f"{child_name}_{shape_name_count[child_name]}"
                                                
                                                # 立即分配count_src
                                                count += 1
                                                group_items_data.append({
                                                    "count_src": count,
                                                    "sheet": sheet.name,
                                                    "shape_name": child_name,
                                                    "unique_shape_id": unique_shape_id,
                                                    "shape_index": -1,  # Negative indicates group child
                                                    "group_name": group_name,
                                                    "group_index": group_index,
                                                    "child_path": item_path,
                                                    "value": text_value,
                                                    "original_value": text_value,
                                                    "type": "group_textbox"
                                                })
                                    except Exception as child_error:
                                        app_logger.warning(f"Error processing group child {path}/{i}: {str(child_error)}")
                                        continue  # Continue with next item
                        except Exception as group_error:
                            app_logger.warning(f"Error accessing group items: {str(group_error)}")
                            
                        return group_items_data
                    
                    # Process individual shapes
                    for shape_idx, shape in enumerate(shapes):
                        try:
                            # Check if shape is a group
                            is_group = False
                            try:
                                if hasattr(shape, 'type') and 'group' in str(shape.type).lower():
                                    is_group = True
                            except:
                                try:
                                    if shape.api.Type == 6:  # 6 is Excel's group type
                                        is_group = True
                                except:
                                    pass
                            
                            if is_group:
                                # Process group and its nested items
                                try:
                                    group_name = shape.name
                                    group_items_data = process_group_items(shape, shape_idx, group_name)
                                    sheet_data.extend(group_items_data)
                                except Exception as group_error:
                                    app_logger.warning(f"Error processing group {shape_idx}: {str(group_error)}")
                            else:
                                # Process individual shape
                                try:
                                    if hasattr(shape, 'text') and shape.text:
                                        text_value = shape.text
                                        
                                        # Skip formula text in shapes
                                        if isinstance(text_value, str) and text_value.strip().startswith('='):
                                            continue
                                            
                                        if not should_translate(text_value):
                                            continue
                                        
                                        text_value = str(text_value).replace("\n", "␊").replace("\r", "␍")
                                        
                                        # Create unique identifier
                                        original_shape_name = shape.name
                                        if original_shape_name in shape_name_count:
                                            shape_name_count[original_shape_name] += 1
                                        else:
                                            shape_name_count[original_shape_name] = 1
                                        unique_shape_id = f"{original_shape_name}_{shape_name_count[original_shape_name]}"
                                        
                                        # 立即分配count_src
                                        count += 1
                                        sheet_data.append({
                                            "count_src": count,
                                            "sheet": sheet.name,
                                            "shape_name": original_shape_name,
                                            "unique_shape_id": unique_shape_id,
                                            "shape_index": shape_idx,
                                            "value": text_value,
                                            "original_value": text_value,  # 保存原始值
                                            "type": "textbox"
                                        })
                                except Exception as shape_error:
                                    app_logger.warning(f"Error processing individual shape {shape_idx}: {str(shape_error)}")
                        except Exception as e:
                            app_logger.warning(f"Error processing shape #{shape_idx}: {str(e)}")
                            continue  # Continue with next shape
                    
                    app_logger.info(f"Extracted {len([item for item in sheet_data if item['type'] in ['textbox', 'group_textbox']])} textboxes from sheet '{sheet.name}'")
                    
            except Exception as e:
                app_logger.error(f"Error processing shapes in sheet {sheet.name}: {str(e)}")
                # Don't let shape processing errors stop cell processing
                
            return sheet_data
            
        # Process all sheets
        for sheet in sheets:
            try:
                sheet_data = process_sheet(sheet)
                # 直接添加到cell_data，不需要再分配count_src
                cell_data.extend(sheet_data)
            except Exception as e:
                app_logger.error(f"Error processing sheet {sheet.name}: {str(e)}")
                # Continue with next sheet
                
        app_logger.info(f"Extracted {len(cell_data)} items using xlwings")
        
    finally:
        try:
            wb.close()
            app.quit()
        except Exception as e:
            app_logger.error(f"Error closing workbook: {str(e)}")
    
    # Extract SmartArt content using ZIP operations
    try:
        count = _extract_smartart_from_excel(file_path, cell_data, count)
    except Exception as e:
        app_logger.error(f"Error extracting SmartArt from Excel: {str(e)}")
    
    # Extract drawing content using ZIP operations (for complex nested textboxes)
    try:
        count = _extract_drawing_content_from_excel(file_path, cell_data, count)
    except Exception as e:
        app_logger.error(f"Error extracting drawing content from Excel: {str(e)}")
    
    # Save to JSON
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(cell_data, json_file, ensure_ascii=False, indent=4)

    app_logger.info(f"Total extracted items: {len(cell_data)}")
    return json_path


def _extract_drawing_content_from_excel(file_path: str, content_data: List, count: int) -> int:
    """Extract text from drawing files in Excel (including complex nested textboxes)."""
    try:
        # Excel drawing namespaces
        namespaces = {
            'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
            'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        }
        
        with ZipFile(file_path, 'r') as excel_zip:
            # Find drawing files in Excel (they are in xl/drawings/)
            drawing_files = [name for name in excel_zip.namelist() 
                            if name.startswith('xl/drawings/drawing') and name.endswith('.xml')]
            drawing_files.sort()
            
            app_logger.info(f"Found {len(drawing_files)} drawing files in Excel")
            
            # Also need to map drawings to sheets
            sheet_drawing_map = _get_sheet_drawing_map(excel_zip)
            
            for drawing_path in drawing_files:
                try:
                    # Extract drawing number from path
                    drawing_match = re.search(r'drawing(\d+)\.xml', drawing_path)
                    if not drawing_match:
                        continue
                    
                    drawing_index = int(drawing_match.group(1))
                    
                    # Try to find which sheet this drawing belongs to
                    sheet_name = sheet_drawing_map.get(drawing_index, f"Sheet{drawing_index}")
                    
                    drawing_xml = excel_zip.read(drawing_path)
                    drawing_tree = etree.fromstring(drawing_xml)
                    
                    # Extract text from all textboxes in the drawing
                    count = _extract_textboxes_from_drawing(
                        drawing_tree, namespaces, content_data, count, 
                        drawing_path, sheet_name, drawing_index
                    )
                                    
                except Exception as e:
                    app_logger.error(f"Failed to extract drawing content from {drawing_path}: {e}")
                    continue
                    
    except Exception as e:
        app_logger.error(f"Failed to extract drawing content from Excel: {e}")
    
    drawing_items = sum(1 for item in content_data if item.get('type') == 'excel_drawing')
    app_logger.info(f"Extracted {drawing_items} drawing textbox items from Excel")
    return count


def _get_sheet_drawing_map(excel_zip) -> Dict[int, str]:
    """Get mapping of drawing index to sheet name."""
    sheet_drawing_map = {}
    
    try:
        # Read workbook.xml to get sheet information
        if 'xl/workbook.xml' in excel_zip.namelist():
            workbook_xml = excel_zip.read('xl/workbook.xml')
            workbook_tree = etree.fromstring(workbook_xml)
            
            # Get sheet information
            sheets = workbook_tree.xpath('.//sheet', namespaces={'': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'})
            
            for sheet in sheets:
                sheet_name = sheet.get('name')
                sheet_id = sheet.get('sheetId')
                r_id = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                
                # Try to find the corresponding drawing
                worksheet_path = f'xl/worksheets/sheet{sheet_id}.xml'
                if worksheet_path in excel_zip.namelist():
                    try:
                        worksheet_xml = excel_zip.read(worksheet_path)
                        worksheet_tree = etree.fromstring(worksheet_xml)
                        
                        # Look for drawing reference
                        drawings = worksheet_tree.xpath('.//drawing', namespaces={'': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'})
                        for drawing in drawings:
                            drawing_r_id = drawing.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                            if drawing_r_id:
                                # Map drawing index to sheet name (simplified mapping)
                                if sheet_id:
                                    sheet_drawing_map[int(sheet_id)] = sheet_name
                                    
                    except Exception as e:
                        app_logger.warning(f"Error processing worksheet {worksheet_path}: {e}")
                        
    except Exception as e:
        app_logger.warning(f"Error getting sheet-drawing mapping: {e}")
    
    return sheet_drawing_map


def _extract_textboxes_from_drawing(drawing_tree, namespaces: Dict, content_data: List, 
                                   count: int, drawing_path: str, sheet_name: str, 
                                   drawing_index: int) -> int:
    """Extract text from all textboxes in a drawing, including nested groups."""
    
    # Find all two-cell anchors (main containers)
    anchors = drawing_tree.xpath('.//xdr:twoCellAnchor', namespaces=namespaces)
    
    for anchor_idx, anchor in enumerate(anchors):
        count = _process_drawing_anchor(
            anchor, namespaces, content_data, count, 
            drawing_path, sheet_name, drawing_index, anchor_idx
        )
    
    return count


def _process_drawing_anchor(anchor, namespaces: Dict, content_data: List, count: int,
                           drawing_path: str, sheet_name: str, drawing_index: int, 
                           anchor_idx: int) -> int:
    """Process a single anchor element and extract all text content."""
    
    # Process direct textboxes (xdr:sp with text)
    textboxes = anchor.xpath('.//xdr:sp[.//xdr:txBody]', namespaces=namespaces)
    for tb_idx, textbox in enumerate(textboxes):
        count = _process_drawing_textbox(
            textbox, namespaces, content_data, count, drawing_path, 
            sheet_name, drawing_index, anchor_idx, tb_idx, "textbox"
        )
    
    # Process group shapes recursively
    groups = anchor.xpath('.//xdr:grpSp', namespaces=namespaces)
    for group_idx, group in enumerate(groups):
        count = _process_drawing_group(
            group, namespaces, content_data, count, drawing_path,
            sheet_name, drawing_index, anchor_idx, group_idx, ""
        )
    
    return count


def _process_drawing_group(group, namespaces: Dict, content_data: List, count: int,
                          drawing_path: str, sheet_name: str, drawing_index: int,
                          anchor_idx: int, group_idx: int, group_path: str) -> int:
    """Process a group shape and extract text from all nested elements."""
    
    current_path = f"{group_path}/G{group_idx}" if group_path else f"G{group_idx}"
    
    # Get group name/id for identification
    group_name = ""
    try:
        cNvPr = group.xpath('.//xdr:cNvPr', namespaces=namespaces)
        if cNvPr:
            group_name = cNvPr[0].get('name', f"Group_{group_idx}")
    except:
        group_name = f"Group_{group_idx}"
    
    # Process direct textboxes in this group
    textboxes = group.xpath('./xdr:sp[.//xdr:txBody]', namespaces=namespaces)
    for tb_idx, textbox in enumerate(textboxes):
        count = _process_drawing_textbox(
            textbox, namespaces, content_data, count, drawing_path,
            sheet_name, drawing_index, anchor_idx, tb_idx, "group_textbox",
            group_name, current_path
        )
    
    # Process nested groups recursively
    nested_groups = group.xpath('./xdr:grpSp', namespaces=namespaces)
    for nested_idx, nested_group in enumerate(nested_groups):
        count = _process_drawing_group(
            nested_group, namespaces, content_data, count, drawing_path,
            sheet_name, drawing_index, anchor_idx, nested_idx, current_path
        )
    
    return count


def _process_drawing_textbox(textbox, namespaces: Dict, content_data: List, count: int,
                            drawing_path: str, sheet_name: str, drawing_index: int,
                            anchor_idx: int, tb_idx: int, textbox_type: str,
                            group_name: str = "", group_path: str = "") -> int:
    """Process a single textbox and extract its text content."""
    
    try:
        # Get textbox name/id
        textbox_name = ""
        textbox_id = ""
        try:
            cNvPr = textbox.xpath('.//xdr:cNvPr', namespaces=namespaces)
            if cNvPr:
                textbox_name = cNvPr[0].get('name', f"TextBox_{tb_idx}")
                textbox_id = cNvPr[0].get('id', str(tb_idx))
        except:
            textbox_name = f"TextBox_{tb_idx}"
            textbox_id = str(tb_idx)
        
        # Extract text from txBody
        tx_bodies = textbox.xpath('.//xdr:txBody', namespaces=namespaces)
        
        for tx_body_idx, tx_body in enumerate(tx_bodies):
            paragraphs = tx_body.xpath('.//a:p', namespaces=namespaces)
            
            for p_idx, paragraph in enumerate(paragraphs):
                # Get all text runs in this paragraph
                text_runs = paragraph.xpath('.//a:r', namespaces=namespaces)
                
                if not text_runs:
                    # Check if there's direct text in the paragraph
                    text_nodes = paragraph.xpath('.//a:t', namespaces=namespaces)
                    if text_nodes and text_nodes[0].text:
                        merged_text = text_nodes[0].text.strip()
                        if merged_text and should_translate(merged_text):
                            count += 1
                            content_data.append({
                                "count_src": count,
                                "sheet": sheet_name,
                                "drawing_path": drawing_path,
                                "drawing_index": drawing_index,
                                "anchor_index": anchor_idx,
                                "textbox_index": tb_idx,
                                "tx_body_index": tx_body_idx,
                                "paragraph_index": p_idx,
                                "textbox_name": textbox_name,
                                "textbox_id": textbox_id,
                                "group_name": group_name,
                                "group_path": group_path,
                                "type": "excel_drawing",
                                "textbox_type": textbox_type,
                                "value": merged_text.replace("\n", "␊").replace("\r", "␍"),
                                "original_value": merged_text.replace("\n", "␊").replace("\r", "␍"),
                                "original_text": merged_text,
                                "run_texts": [merged_text],
                                "run_styles": [{}],
                                "run_lengths": [len(merged_text)],
                                "xpath": f".//xdr:twoCellAnchor[{anchor_idx + 1}]//xdr:sp[{tb_idx + 1}]//xdr:txBody[{tx_body_idx + 1}]//a:p[{p_idx + 1}]"
                            })
                    continue
                
                # Process runs and preserve exact spacing
                run_info = _process_drawing_text_runs(text_runs, namespaces)
                
                if not run_info['merged_text'].strip():
                    continue
                
                # Only process if there's meaningful text content and it should be translated
                if should_translate(run_info['merged_text']):
                    count += 1
                    content_data.append({
                        "count_src": count,
                        "sheet": sheet_name,
                        "drawing_path": drawing_path,
                        "drawing_index": drawing_index,
                        "anchor_index": anchor_idx,
                        "textbox_index": tb_idx,
                        "tx_body_index": tx_body_idx,
                        "paragraph_index": p_idx,
                        "textbox_name": textbox_name,
                        "textbox_id": textbox_id,
                        "group_name": group_name,
                        "group_path": group_path,
                        "type": "excel_drawing",
                        "textbox_type": textbox_type,
                        "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                        "original_value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                        "run_texts": run_info['run_texts'],
                        "run_styles": run_info['run_styles'],
                        "run_lengths": run_info['run_lengths'],
                        "original_text": run_info['merged_text'],
                        "xpath": f".//xdr:twoCellAnchor[{anchor_idx + 1}]//xdr:sp[{tb_idx + 1}]//xdr:txBody[{tx_body_idx + 1}]//a:p[{p_idx + 1}]"
                    })
                    
    except Exception as e:
        app_logger.warning(f"Error processing drawing textbox: {e}")
    
    return count


def _process_drawing_text_runs(text_runs, namespaces: dict) -> dict:
    """Process text runs for Excel drawing and preserve exact spacing and formatting."""
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
        run_styles.append(_extract_drawing_run_style(text_run, namespaces))
    
    return {
        'merged_text': merged_text,
        'run_texts': run_texts,
        'run_styles': run_styles,
        'run_lengths': run_lengths
    }


def _extract_drawing_run_style(text_run, namespaces: dict) -> dict:
    """Extract comprehensive style information from a text run in Excel drawing."""
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
        app_logger.warning(f"Failed to extract drawing style information: {e}")
    
    return style_info


def _extract_smartart_from_excel(file_path: str, content_data: List, count: int) -> int:
    """Extract text from SmartArt diagrams in Excel."""
    try:
        # Excel SmartArt namespaces (similar to PowerPoint)
        namespaces = {
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
            'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
            'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram',
            'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        }
        
        with ZipFile(file_path, 'r') as excel_zip:
            # Find SmartArt diagram files in Excel (they are in xl/diagrams/)
            diagram_drawings = [name for name in excel_zip.namelist() 
                               if name.startswith('xl/diagrams/drawing') and name.endswith('.xml')]
            diagram_drawings.sort()
            
            app_logger.info(f"Found {len(diagram_drawings)} SmartArt diagram files in Excel")
            
            for drawing_path in diagram_drawings:
                try:
                    # Extract diagram number from path
                    diagram_match = re.search(r'drawing(\d+)\.xml', drawing_path)
                    if not diagram_match:
                        continue
                    
                    diagram_index = int(diagram_match.group(1))
                    
                    drawing_xml = excel_zip.read(drawing_path)
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
                                run_info = _process_excel_smartart_text_runs(text_runs, namespaces)
                                
                                if not run_info['merged_text'].strip():
                                    continue
                                
                                # Only process if there's meaningful text content and it should be translated
                                if should_translate(run_info['merged_text']):
                                    count += 1
                                    content_data.append({
                                        "count_src": count,
                                        "diagram_index": diagram_index,
                                        "shape_index": shape_index,
                                        "tx_body_index": tx_body_index,
                                        "paragraph_index": p_index,
                                        "model_id": model_id,
                                        "type": "excel_smartart",
                                        "value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),
                                        "original_value": run_info['merged_text'].replace("\n", "␊").replace("\r", "␍"),  # 保存原始值
                                        "run_texts": run_info['run_texts'],
                                        "run_styles": run_info['run_styles'],
                                        "run_lengths": run_info['run_lengths'],
                                        "drawing_path": drawing_path,
                                        "original_text": run_info['merged_text'],
                                        "xpath": f".//dsp:sp[{shape_index + 1}]//dsp:txBody[{tx_body_index + 1}]//a:p[{p_index + 1}]"
                                    })
                                    
                except Exception as e:
                    app_logger.error(f"Failed to extract SmartArt from {drawing_path}: {e}")
                    continue
                    
    except Exception as e:
        app_logger.error(f"Failed to extract SmartArt from Excel: {e}")
    
    app_logger.info(f"Extracted {sum(1 for item in content_data if item.get('type') == 'excel_smartart')} SmartArt items from Excel")
    return count


def _process_excel_smartart_text_runs(text_runs, namespaces: dict) -> dict:
    """Process text runs for Excel SmartArt and preserve exact spacing and formatting."""
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
        run_styles.append(_extract_excel_smartart_run_style(text_run, namespaces))
    
    return {
        'merged_text': merged_text,
        'run_texts': run_texts,
        'run_styles': run_styles,
        'run_lengths': run_lengths
    }


def _extract_excel_smartart_run_style(text_run, namespaces: dict) -> dict:
    """Extract comprehensive style information from a text run in Excel SmartArt."""
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
        app_logger.warning(f"Failed to extract Excel SmartArt style information: {e}")
    
    return style_info


def sanitize_sheet_name(sheet_name):
    """
    Clean sheet name by removing/replacing invalid characters.
    Excel doesn't allow these characters in sheet names: forward slash, backslash, question mark, asterisk, square brackets, colon
    """
    # Replace invalid characters with safe alternatives
    invalid_chars = ['/', '\\', '?', '*', '[', ']', ':']
    sanitized_name = sheet_name
    for char in invalid_chars:
        sanitized_name = sanitized_name.replace(char, '-')
    
    # Excel sheet names are limited to 31 characters
    if len(sanitized_name) > 31:
        sanitized_name = sanitized_name[:31]
    
    # Ensure the sheet name is not empty
    if not sanitized_name.strip():
        sanitized_name = "Sheet"
    
    return sanitized_name


def write_translated_content_to_excel(file_path, original_json_path, translated_json_path, result_dir):
    # Load JSON data
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    # 验证数据完整性
    app_logger.info(f"Original data count: {len(original_data)}")
    app_logger.info(f"Translation data count: {len(translated_data)}")
    
    # 创建count_src到翻译的映射
    translations = {}
    for item in translated_data:
        count_src = str(item["count_src"])
        translations[count_src] = item["translated"]
    
    app_logger.info(f"Translation dictionary count: {len(translations)}")
    
    # 验证原始数据和翻译数据的匹配情况
    missing_translations = []
    original_count_srcs = set(str(item["count_src"]) for item in original_data)
    translated_count_srcs = set(translations.keys())
    
    missing_in_translation = original_count_srcs - translated_count_srcs
    extra_in_translation = translated_count_srcs - original_count_srcs
    
    if missing_in_translation:
        app_logger.warning(f"Missing translations for count_src: {sorted(missing_in_translation)}")
    if extra_in_translation:
        app_logger.warning(f"Extra translations for count_src: {sorted(extra_in_translation)}")
    
    # Collect sheet name translations
    sheet_name_translations = {}
    for cell_info in original_data:
        if cell_info.get("type") == "sheet_name":
            count_src = str(cell_info["count_src"])
            original_sheet_name = cell_info["sheet"]
            translated_sheet_name = translations.get(count_src)
            if translated_sheet_name:
                # Sanitize the sheet name to avoid invalid characters
                sanitized_name = sanitize_sheet_name(translated_sheet_name.replace("␊", "\n").replace("␍", "\r"))
                sheet_name_translations[original_sheet_name] = sanitized_name
                
                # Log if the name was changed
                if sanitized_name != translated_sheet_name.replace("␊", "\n").replace("␍", "\r"):
                    app_logger.warning(f"Sheet name '{translated_sheet_name}' contains invalid characters and was changed to '{sanitized_name}'")

    # Organize data by sheet and type
    sheets_data = {}
    smartart_items = []
    drawing_items = []
    
    for cell_info in original_data:
        if cell_info.get("type") == "sheet_name":
            continue
        elif cell_info.get("type") == "excel_smartart":
            smartart_items.append(cell_info)
            continue
        elif cell_info.get("type") == "excel_drawing":
            drawing_items.append(cell_info)
            continue
            
        count_src = str(cell_info["count_src"])
        sheet_name = cell_info["sheet"]
        
        if sheet_name not in sheets_data:
            sheets_data[sheet_name] = {
                "cells": [],
                "textboxes": []
            }
            
        translated_value = translations.get(count_src)
        if translated_value is None:
            missing_translations.append(count_src)
            app_logger.warning(
                f"Translation missing for count_src {count_src}. Sheet: {sheet_name}, Original text: '{cell_info['value'][:100]}...'"
            )
            # 使用原文作为翻译
            translated_value = cell_info['value']
            
        translated_value = translated_value.replace("␊", "\n").replace("␍", "\r")
        
        if cell_info.get("type") == "cell":
            cell_data = {
                "row": cell_info["row"],
                "column": cell_info["column"],
                "value": translated_value,
                "is_merged": cell_info.get("is_merged", False),
                "count_src": count_src,  # 添加count_src用于验证
                "original_value": cell_info.get("original_value", cell_info["value"])  # 使用保存的原始值
            }
            
            # 添加合并单元格信息
            if cell_info.get("is_merged"):
                cell_data["merge_start_row"] = cell_info.get("merge_start_row")
                cell_data["merge_start_col"] = cell_info.get("merge_start_col")
                cell_data["merge_end_row"] = cell_info.get("merge_end_row")
                cell_data["merge_end_col"] = cell_info.get("merge_end_col")
            
            sheets_data[sheet_name]["cells"].append(cell_data)
        else:
            textbox_data = cell_info.copy()
            textbox_data["value"] = translated_value
            textbox_data["original_value"] = cell_info.get("original_value", cell_info["value"])
            sheets_data[sheet_name]["textboxes"].append(textbox_data)
    
    if missing_translations:
        app_logger.warning(f"Found {len(missing_translations)} missing translations, using original text")
    
    # Open Excel application
    app = xw.App(visible=False)
    app.screen_updating = False
    app.display_alerts = False
    
    try:
        wb = app.books.open(file_path)
        
        # Handle sheet renaming
        original_to_translated_sheet_map = {}
        new_sheet_names = []
        
        for sheet_name, data in sheets_data.items():
            translated_sheet_name = sheet_name_translations.get(sheet_name)
            if translated_sheet_name:
                original_to_translated_sheet_map[sheet_name] = translated_sheet_name
                new_sheet_names.append((sheet_name, translated_sheet_name))
        
        existing_names = set(sheet.name for sheet in wb.sheets)
        temp_names = {}
        
        # First pass: Use temporary names to avoid conflicts
        for original_name, new_name in new_sheet_names:
            if new_name in existing_names and new_name != original_name:
                temp_name = f"temp_{original_name}_{hash(original_name) % 10000}"
                temp_names[original_name] = temp_name
        
        for original_name, temp_name in temp_names.items():
            try:
                wb.sheets[original_name].name = temp_name
                app_logger.info(f"Temporarily renamed sheet '{original_name}' to '{temp_name}'")
            except Exception as e:
                app_logger.warning(f"Error temporarily renaming sheet '{original_name}' to '{temp_name}': {str(e)}")
        
        # Second pass: Rename to final translated names
        for original_name, new_name in new_sheet_names:
            actual_original_name = temp_names.get(original_name, original_name)
            try:
                # Skip renaming if the names are identical
                if actual_original_name == new_name:
                    app_logger.info(f"Sheet '{original_name}' translation is identical to original, skipping rename")
                    continue
                    
                wb.sheets[actual_original_name].name = new_name
                app_logger.info(f"Successfully renamed sheet '{original_name}' to '{new_name}'")
            except Exception as e:
                app_logger.warning(f"Error renaming sheet '{original_name}' to '{new_name}': {str(e)}")
                # If renaming failed but we used a temporary name, try to restore the original name
                if original_name in temp_names:
                    try:
                        wb.sheets[actual_original_name].name = original_name
                        app_logger.info(f"Restored original sheet name '{original_name}'")
                    except Exception as restore_err:
                        app_logger.error(f"Failed to restore original sheet name '{original_name}': {str(restore_err)}")

        # Update sheet data references to use translated names
        updated_sheets_data = {}
        for sheet_name, data in sheets_data.items():
            actual_sheet_name = sheet_name_translations.get(sheet_name, sheet_name)
            # Only use the translated name if it exists in the workbook (rename was successful)
            if actual_sheet_name in [sheet.name for sheet in wb.sheets]:
                updated_sheets_data[actual_sheet_name] = data
            else:
                # If rename failed, use the original name
                updated_sheets_data[sheet_name] = data
        
        # Process cell and shape content
        for sheet_name, data in updated_sheets_data.items():
            try:
                sheet = wb.sheets[sheet_name]
                
                # Process cells - improved approach with validation
                app_logger.info(f"Processing {len(data['cells'])} cells in sheet '{sheet_name}'")
                
                # Sort cells by row and column to ensure proper order
                cells_to_process = sorted(data["cells"], key=lambda x: (x["row"], x["column"]))
                
                successful_updates = 0
                failed_updates = 0
                
                # 创建位置到count_src的映射，用于调试
                position_to_count = {}
                for cell_data in cells_to_process:
                    position_key = f"{cell_data['row']},{cell_data['column']}"
                    position_to_count[position_key] = cell_data['count_src']
                
                # 分批处理单元格以提高性能
                batch_size = 1000
                for batch_start in range(0, len(cells_to_process), batch_size):
                    batch_end = min(batch_start + batch_size, len(cells_to_process))
                    app_logger.info(f"Processing cell batch {batch_start//batch_size + 1}/{(len(cells_to_process)-1)//batch_size + 1}")
                    
                    for cell_data in cells_to_process[batch_start:batch_end]:
                        try:
                            row = cell_data["row"]
                            column = cell_data["column"]
                            value = cell_data["value"]
                            is_merged = cell_data.get("is_merged", False)
                            count_src = cell_data.get("count_src", "unknown")
                            
                            # 验证单元格位置的合理性
                            if row < 1 or column < 1:
                                app_logger.warning(f"Invalid cell position ({row}, {column}) for count_src {count_src}, skipping")
                                failed_updates += 1
                                continue
                            
                            # 对于合并单元格，确保我们更新的是正确的位置
                            if is_merged:
                                merge_start_row = cell_data.get("merge_start_row", row)
                                merge_start_col = cell_data.get("merge_start_col", column)
                                
                                # 确保我们更新的是合并单元格的左上角
                                if row != merge_start_row or column != merge_start_col:
                                    app_logger.warning(f"Cell ({row}, {column}) count_src {count_src} is merged but not at top-left position. Using ({merge_start_row}, {merge_start_col}) instead")
                                    row = merge_start_row
                                    column = merge_start_col
                            
                            # 可选的验证：检查当前单元格值是否符合预期
                            try:
                                current_value = sheet.cells(row, column).value
                                if current_value is not None:
                                    current_str = str(current_value).replace("\n", "␊").replace("\r", "␍")
                                    original_str = cell_data.get("original_value", "")
                                    # 如果当前值与预期的原始值差异很大，记录警告
                                    if len(current_str) > 0 and len(original_str) > 0:
                                        if current_str.strip() != original_str.strip():
                                            app_logger.debug(f"Cell ({row}, {column}) count_src {count_src} current value differs from expected.")
                                            app_logger.debug(f"Current: '{current_str[:100]}...'")
                                            app_logger.debug(f"Expected: '{original_str[:100]}...'")
                            except:
                                pass  # 忽略验证错误
                            
                            # Update the cell
                            sheet.cells(row, column).value = value
                            successful_updates += 1
                            
                            # 每100个成功更新记录一次进度
                            if successful_updates % 100 == 0:
                                app_logger.debug(f"Successfully updated {successful_updates} cells in sheet '{sheet_name}'")
                            
                        except Exception as cell_error:
                            failed_updates += 1
                            app_logger.warning(f"Error updating cell ({row}, {column}) count_src {count_src}: {str(cell_error)}")
                            continue
                
                app_logger.info(f"Cell processing completed for sheet '{sheet_name}': {successful_updates} successful, {failed_updates} failed")
                
                # Process textboxes
                app_logger.info(f"Processing {len(data['textboxes'])} textboxes in sheet '{sheet_name}'")
                
                # Get all shapes in the sheet
                try:
                    all_shapes = list(sheet.shapes)
                except Exception as shapes_error:
                    app_logger.warning(f"Error getting shapes from sheet '{sheet_name}': {str(shapes_error)}")
                    all_shapes = []
                
                # Split textboxes by type
                normal_textboxes = [tb for tb in data["textboxes"] if tb.get("type") == "textbox"]
                group_textboxes = [tb for tb in data["textboxes"] if tb.get("type") == "group_textbox"]
                
                textbox_success = 0
                textbox_failed = 0
                
                # Process normal textboxes
                for textbox in normal_textboxes:
                    try:
                        matched = False
                        shape_index = textbox.get("shape_index")
                        count_src = textbox.get("count_src", "unknown")
                        
                        # Method 1: Find by index
                        if shape_index is not None and 0 <= shape_index < len(all_shapes):
                            try:
                                shape = all_shapes[shape_index]
                                if hasattr(shape, 'text'):
                                    shape.text = textbox["value"]
                                    matched = True
                                    textbox_success += 1
                                    app_logger.debug(f"Updated textbox by index {shape_index}, count_src {count_src}")
                            except Exception as e:
                                app_logger.warning(f"Error updating shape by index {shape_index}, count_src {count_src}: {str(e)}")
                        
                        # Method 2: Find by unique ID
                        if not matched and textbox.get("unique_shape_id"):
                            try:
                                original_name = textbox["shape_name"]
                                same_name_shapes = [s for s in all_shapes if s.name == original_name]
                                unique_id_parts = textbox["unique_shape_id"].split("_")
                                if len(unique_id_parts) > 1:
                                    id_number = int(unique_id_parts[-1])
                                    if 1 <= id_number <= len(same_name_shapes):
                                        shape = same_name_shapes[id_number - 1]
                                        if hasattr(shape, 'text'):
                                            shape.text = textbox["value"]
                                            matched = True
                                            textbox_success += 1
                                            app_logger.debug(f"Updated shape by unique ID: {textbox['unique_shape_id']}, count_src {count_src}")
                            except Exception as e:
                                app_logger.warning(f"Error updating shape with unique ID {textbox['unique_shape_id']}, count_src {count_src}: {str(e)}")
                        
                        # Method 3: Find by name
                        if not matched:
                            try:
                                for shape in all_shapes:
                                    if shape.name == textbox["shape_name"]:
                                        if hasattr(shape, 'text'):
                                            shape.text = textbox["value"]
                                            matched = True
                                            textbox_success += 1
                                            app_logger.debug(f"Updated shape by name: {textbox['shape_name']}, count_src {count_src}")
                                            break
                            except Exception as e:
                                app_logger.warning(f"Error updating shape by name {textbox['shape_name']}, count_src {count_src}: {str(e)}")
                        
                        if not matched:
                            textbox_failed += 1
                            app_logger.warning(f"Could not find shape to update: {textbox.get('shape_name', 'unknown')}, count_src {count_src}")
                            
                    except Exception as textbox_error:
                        textbox_failed += 1
                        app_logger.warning(f"Error processing textbox count_src {textbox.get('count_src', 'unknown')}: {str(textbox_error)}")
                        continue
                
                # Process group textboxes with nested path support
                for textbox in group_textboxes:
                    try:
                        count_src = textbox.get("count_src", "unknown")
                        # Find the group
                        group_name = textbox.get("group_name")
                        group_index = textbox.get("group_index")
                        child_path = textbox.get("child_path")
                        
                        if not child_path:
                            child_path = str(textbox.get("child_index", ""))
                        
                        # Try to find the group
                        group = None
                        
                        # Method 1: Find by index
                        if group_index is not None and 0 <= group_index < len(all_shapes):
                            try:
                                group = all_shapes[group_index]
                            except:
                                pass
                        
                        # Method 2: Find by name
                        if not group and group_name:
                            for shape in all_shapes:
                                if shape.name == group_name:
                                    group = shape
                                    break
                        
                        # If group found, navigate to the child using path
                        if group and child_path and hasattr(group.api, 'GroupItems'):
                            # Function to navigate nested groups using path
                            def navigate_to_child(parent_group, path):
                                path_parts = path.split('/')
                                current_item = parent_group
                                
                                for part in path_parts:
                                    try:
                                        # Convert path part to index (1-based in Excel)
                                        idx = int(part)
                                        if hasattr(current_item.api, 'GroupItems'):
                                            items = current_item.api.GroupItems
                                            if 1 <= idx <= items.Count:
                                                current_item = items.Item(idx)
                                            else:
                                                return None
                                        else:
                                            return None
                                    except:
                                        return None
                                
                                return current_item
                            
                            # Navigate to child using path
                            child_item = navigate_to_child(group, child_path)
                            
                            if child_item:
                                # Try to update text
                                updated = False
                                
                                # Method 1: TextFrame
                                try:
                                    if hasattr(child_item, 'TextFrame') and child_item.TextFrame.HasText:
                                        child_item.TextFrame.Characters().Text = textbox["value"]
                                        updated = True
                                        textbox_success += 1
                                        app_logger.debug(f"Updated group '{group_name}' child with path {child_path} using TextFrame, count_src {count_src}")
                                except:
                                    pass
                                
                                # Method 2: TextFrame2
                                if not updated:
                                    try:
                                        if hasattr(child_item, 'TextFrame2'):
                                            child_item.TextFrame2.TextRange.Text = textbox["value"]
                                            updated = True
                                            textbox_success += 1
                                            app_logger.debug(f"Updated group '{group_name}' child with path {child_path} using TextFrame2, count_src {count_src}")
                                    except:
                                        pass
                                
                                if not updated:
                                    textbox_failed += 1
                                    app_logger.warning(f"Could not update group '{group_name}' child with path {child_path}, count_src {count_src}")
                            else:
                                textbox_failed += 1
                                app_logger.warning(f"Could not navigate to child with path {child_path} in group '{group_name}', count_src {count_src}")
                        else:
                            textbox_failed += 1
                            app_logger.warning(f"Could not find group '{group_name}' or it lacks GroupItems, count_src {count_src}")
                    except Exception as e:
                        textbox_failed += 1
                        count_src = textbox.get("count_src", "unknown")
                        app_logger.warning(f"Error processing group shape, group: {textbox.get('group_name')}, path: {textbox.get('child_path')}, count_src {count_src}: {str(e)}")
                        continue
                
                app_logger.info(f"Textbox processing completed for sheet '{sheet_name}': {textbox_success} successful, {textbox_failed} failed")
                
            except Exception as e:
                app_logger.error(f"Error processing sheet {sheet_name}: {str(e)}")
                continue
        
        # Save the workbook first
        result_folder = os.path.join(result_dir)
        os.makedirs(result_folder, exist_ok=True)
        
        result_path = os.path.join(
            result_folder,
            f"{os.path.splitext(os.path.basename(file_path))[0]}_translated{os.path.splitext(file_path)[1]}"
        )
        
        try:
            wb.save(result_path)
            app_logger.info(f"Translated Excel (without drawing/SmartArt) saved to: {result_path}")
        except Exception as e:
            app_logger.error(f"Failed to save translated Excel: {str(e)}")
            # Try saving with a different filename if there was an error
            fallback_path = os.path.join(
                result_folder,
                f"{os.path.splitext(os.path.basename(file_path))[0]}_translated_fallback{os.path.splitext(file_path)[1]}"
            )
            try:
                wb.save(fallback_path)
                app_logger.info(f"Translated Excel saved to fallback path: {fallback_path}")
                result_path = fallback_path
            except Exception as e2:
                app_logger.error(f"Failed to save translated Excel to fallback path: {str(e2)}")
                raise
        
    finally:
        try:
            wb.close()
            app.quit()
        except Exception as e:
            app_logger.error(f"Error closing workbook or quitting app: {str(e)}")
    
    # Now process SmartArt if there are any SmartArt items
    if smartart_items:
        app_logger.info(f"Processing {len(smartart_items)} SmartArt translations")
        try:
            result_path = _apply_excel_smartart_translations_to_file(result_path, smartart_items, translations)
        except Exception as e:
            app_logger.error(f"Failed to apply SmartArt translations: {str(e)}")
    
    # Now process Drawing if there are any drawing items
    if drawing_items:
        app_logger.info(f"Processing {len(drawing_items)} drawing translations")
        try:
            result_path = _apply_excel_drawing_translations_to_file(result_path, drawing_items, translations)
        except Exception as e:
            app_logger.error(f"Failed to apply drawing translations: {str(e)}")
    
    return result_path


def _apply_excel_drawing_translations_to_file(file_path: str, drawing_items: List[Dict], translations: Dict) -> str:
    """Apply translations to Excel drawing textboxes."""
    if not drawing_items:
        return file_path
    
    app_logger.info(f"Processing {len(drawing_items)} Excel drawing translations")
    
    namespaces = {
        'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    }
    
    # Group items by drawing_index
    items_by_drawing = {}
    for item in drawing_items:
        drawing_index = item['drawing_index']
        if drawing_index not in items_by_drawing:
            items_by_drawing[drawing_index] = []
        items_by_drawing[drawing_index].append(item)
    
    # Create a temporary file to modify the Excel
    temp_excel_path = file_path + ".tmp"
    
    try:
        with ZipFile(file_path, 'r') as original_zip:
            with ZipFile(temp_excel_path, 'w') as new_zip:
                # Copy all files except drawing files that we need to modify
                modified_files = set()
                
                for drawing_index, items in items_by_drawing.items():
                    drawing_path = f"xl/drawings/drawing{drawing_index}.xml"
                    modified_files.add(drawing_path)
                
                # Copy unchanged files
                for item in original_zip.infolist():
                    if item.filename not in modified_files:
                        try:
                            new_zip.writestr(item, original_zip.read(item.filename))
                        except Exception as e:
                            app_logger.warning(f"Failed to copy file {item.filename}: {e}")
                
                # Process and add modified files
                for drawing_index, items in items_by_drawing.items():
                    drawing_path = f"xl/drawings/drawing{drawing_index}.xml"
                    
                    # Process drawing file
                    if drawing_path in original_zip.namelist():
                        try:
                            drawing_xml = original_zip.read(drawing_path)
                            drawing_tree = etree.fromstring(drawing_xml)
                            
                            for item in items:
                                count = str(item['count_src'])
                                translated_text = translations.get(count)
                                
                                if not translated_text:
                                    app_logger.warning(f"Missing translation for Excel drawing count {count}")
                                    continue
                                
                                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                                
                                # Build XPath to find the specific element
                                try:
                                    # Use the stored xpath or build one
                                    xpath = item.get('xpath')
                                    if not xpath:
                                        anchor_idx = item['anchor_index']
                                        tb_idx = item['textbox_index']
                                        tx_body_idx = item['tx_body_index']
                                        p_idx = item['paragraph_index']
                                        xpath = f".//xdr:twoCellAnchor[{anchor_idx + 1}]//xdr:sp[{tb_idx + 1}]//xdr:txBody[{tx_body_idx + 1}]//a:p[{p_idx + 1}]"
                                    
                                    # Find the paragraph element
                                    paragraphs = drawing_tree.xpath(xpath, namespaces=namespaces)
                                    
                                    if paragraphs:
                                        paragraph = paragraphs[0]
                                        _distribute_drawing_text_to_runs(paragraph, translated_text, item, namespaces)
                                        app_logger.info(f"Updated Excel drawing text for drawing {drawing_index}, count_src {count}")
                                    else:
                                        # Fallback: find by other means
                                        success = _fallback_drawing_text_update(drawing_tree, item, translated_text, namespaces)
                                        if success:
                                            app_logger.info(f"Updated Excel drawing text (fallback) for drawing {drawing_index}, count_src {count}")
                                        else:
                                            app_logger.warning(f"Could not find element to update for drawing {drawing_index}, count_src {count}")
                                            
                                except Exception as e:
                                    app_logger.error(f"Failed to update drawing text for count_src {count}: {e}")
                            
                            # Write modified drawing
                            modified_drawing_xml = etree.tostring(drawing_tree, xml_declaration=True, 
                                                                 encoding="UTF-8", standalone="yes")
                            new_zip.writestr(drawing_path, modified_drawing_xml)
                            app_logger.info(f"Saved modified Excel drawing file: {drawing_path}")
                            
                        except Exception as e:
                            app_logger.error(f"Failed to apply Excel drawing translation to {drawing_path}: {e}")
                            # Use original file as fallback
                            try:
                                new_zip.writestr(drawing_path, original_zip.read(drawing_path))
                            except Exception as fallback_e:
                                app_logger.error(f"Failed to copy original drawing file as fallback: {fallback_e}")
        
        # Replace original file with modified file
        shutil.move(temp_excel_path, file_path)
        app_logger.info(f"Excel drawing translations applied successfully")
        return file_path
        
    except Exception as e:
        app_logger.error(f"Failed to apply Excel drawing translations: {e}")
        # Clean up temporary file if it exists
        if os.path.exists(temp_excel_path):
            try:
                os.remove(temp_excel_path)
            except:
                pass
        return file_path


def _fallback_drawing_text_update(drawing_tree, item: Dict, translated_text: str, namespaces: Dict) -> bool:
    """Fallback method to find and update drawing text by matching content."""
    try:
        original_text = item.get('original_text', '').strip()
        if not original_text:
            return False
        
        # Find all text elements and match by content
        all_text_nodes = drawing_tree.xpath('.//a:t', namespaces=namespaces)
        
        for text_node in all_text_nodes:
            if text_node.text and text_node.text.strip() == original_text:
                # Found matching text, update it
                text_node.text = translated_text
                app_logger.debug(f"Updated text via fallback method: '{original_text}' -> '{translated_text[:50]}...'")
                return True
        
        # Try partial matching
        for text_node in all_text_nodes:
            if text_node.text and original_text in text_node.text:
                text_node.text = text_node.text.replace(original_text, translated_text)
                app_logger.debug(f"Updated text via partial matching: '{original_text}' -> '{translated_text[:50]}...'")
                return True
                
        return False
        
    except Exception as e:
        app_logger.warning(f"Error in fallback text update: {e}")
        return False


def _distribute_drawing_text_to_runs(parent_element, translated_text: str, item: Dict, namespaces: Dict):
    """Distribute translated text across multiple runs in Excel drawing, preserving spacing and structure."""
    text_runs = parent_element.xpath('.//a:r', namespaces=namespaces)
    
    if not text_runs:
        # If no runs, try to create one or update direct text
        text_nodes = parent_element.xpath('.//a:t', namespaces=namespaces)
        if text_nodes:
            text_nodes[0].text = translated_text
        return
    
    original_run_texts = item.get('run_texts', [])
    original_run_lengths = item.get('run_lengths', [])
    
    # If we don't have the original structure, fallback to simple distribution
    if not original_run_texts or len(original_run_texts) != len(text_runs):
        app_logger.warning(f"Mismatch in Excel drawing run structure, using simple distribution")
        _simple_drawing_text_distribution(text_runs, translated_text, namespaces)
        return
    
    # Use intelligent distribution based on original structure
    _intelligent_drawing_text_distribution(text_runs, translated_text, original_run_texts, original_run_lengths, namespaces)


def _simple_drawing_text_distribution(text_runs, translated_text: str, namespaces: Dict):
    """Simple fallback distribution method for Excel drawing."""
    if not text_runs:
        return
    
    # Put all translated text in the first run, clear others
    for i, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if text_node:
            if i == 0:
                text_node[0].text = translated_text
            else:
                text_node[0].text = ""


def _intelligent_drawing_text_distribution(text_runs, translated_text: str, original_run_texts: List[str], 
                                         original_run_lengths: List[int], namespaces: Dict):
    """Intelligent text distribution for Excel drawing that preserves spacing and structure."""
    
    # Calculate total length excluding empty runs
    meaningful_runs = [(i, length) for i, length in enumerate(original_run_lengths) if length > 0]
    total_meaningful_length = sum(length for _, length in meaningful_runs)
    
    if total_meaningful_length == 0:
        _simple_drawing_text_distribution(text_runs, translated_text, namespaces)
        return
    
    # Handle special cases for spacing
    translated_chars = list(translated_text)
    char_index = 0
    
    for run_index, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if not text_node:
            continue
            
        original_text = original_run_texts[run_index] if run_index < len(original_run_texts) else ""
        original_length = original_run_lengths[run_index] if run_index < len(original_run_lengths) else 0
        
        # Handle empty or whitespace-only runs
        if original_length == 0 or not original_text.strip():
            # If original run was empty or whitespace, keep it empty
            # unless it was purely whitespace and we need to preserve spacing
            if original_text and not original_text.strip():
                # This run contained only whitespace, try to preserve some spacing
                if char_index < len(translated_chars) and translated_chars[char_index] == ' ':
                    text_node[0].text = ' '
                    char_index += 1
                else:
                    text_node[0].text = ""
            else:
                text_node[0].text = ""
            continue
        
        # Calculate how much text this run should get
        if run_index == len(text_runs) - 1:
            # Last meaningful run gets all remaining text
            remaining_text = ''.join(translated_chars[char_index:])
            text_node[0].text = remaining_text
        else:
            # Calculate proportional distribution
            proportion = original_length / total_meaningful_length
            target_length = max(1, int(len(translated_text) * proportion))
            
            # Try to break at word boundaries
            run_text = ""
            chars_taken = 0
            
            while chars_taken < target_length and char_index < len(translated_chars):
                char = translated_chars[char_index]
                run_text += char
                chars_taken += 1
                char_index += 1
                
                # If we've reached the target length, try to extend to a word boundary
                if chars_taken >= target_length and char_index < len(translated_chars):
                    # Look ahead for word boundary
                    if char != ' ' and translated_chars[char_index] != ' ':
                        # Continue until we find a space or reach the end
                        while (char_index < len(translated_chars) and 
                               translated_chars[char_index] != ' ' and 
                               chars_taken < target_length * 1.5):  # Don't go too far
                            char = translated_chars[char_index]
                            run_text += char
                            chars_taken += 1
                            char_index += 1
                    break
            
            text_node[0].text = run_text


def _apply_excel_smartart_translations_to_file(file_path: str, smartart_items: List[Dict], translations: Dict) -> str:
    """Apply translations to Excel SmartArt diagrams."""
    if not smartart_items:
        return file_path
    
    app_logger.info(f"Processing {len(smartart_items)} Excel SmartArt translations")
    
    namespaces = {
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        'dgm': 'http://schemas.openxmlformats.org/drawingml/2006/diagram',
        'dsp': 'http://schemas.microsoft.com/office/drawing/2008/diagram',
        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    }
    
    # Group items by diagram_index
    items_by_diagram = {}
    for item in smartart_items:
        diagram_index = item['diagram_index']
        if diagram_index not in items_by_diagram:
            items_by_diagram[diagram_index] = []
        items_by_diagram[diagram_index].append(item)
    
    # Create a temporary file to modify the Excel
    temp_excel_path = file_path + ".tmp"
    
    try:
        with ZipFile(file_path, 'r') as original_zip:
            with ZipFile(temp_excel_path, 'w') as new_zip:
                # Copy all files except diagram files that we need to modify
                modified_files = set()
                
                for diagram_index, items in items_by_diagram.items():
                    drawing_path = f"xl/diagrams/drawing{diagram_index}.xml"
                    data_path = f"xl/diagrams/data{diagram_index}.xml"
                    modified_files.add(drawing_path)
                    modified_files.add(data_path)
                
                # Copy unchanged files
                for item in original_zip.infolist():
                    if item.filename not in modified_files:
                        try:
                            new_zip.writestr(item, original_zip.read(item.filename))
                        except Exception as e:
                            app_logger.warning(f"Failed to copy file {item.filename}: {e}")
                
                # Process and add modified files
                for diagram_index, items in items_by_diagram.items():
                    drawing_path = f"xl/diagrams/drawing{diagram_index}.xml"
                    data_path = f"xl/diagrams/data{diagram_index}.xml"
                    
                    # Process drawing file
                    if drawing_path in original_zip.namelist():
                        try:
                            drawing_xml = original_zip.read(drawing_path)
                            drawing_tree = etree.fromstring(drawing_xml)
                            
                            for item in items:
                                count = str(item['count_src'])
                                translated_text = translations.get(count)
                                
                                if not translated_text:
                                    app_logger.warning(f"Missing translation for Excel SmartArt count {count}")
                                    continue
                                
                                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                                
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
                                            _distribute_excel_smartart_text_to_runs(paragraph, translated_text, item, namespaces)
                                            app_logger.info(f"Updated Excel SmartArt drawing text for diagram {diagram_index}, shape {item['shape_index']}, count_src {count}")
                            
                            # Write modified drawing
                            modified_drawing_xml = etree.tostring(drawing_tree, xml_declaration=True, 
                                                                 encoding="UTF-8", standalone="yes")
                            new_zip.writestr(drawing_path, modified_drawing_xml)
                            app_logger.info(f"Saved modified Excel SmartArt drawing file: {drawing_path}")
                            
                        except Exception as e:
                            app_logger.error(f"Failed to apply Excel SmartArt translation to {drawing_path}: {e}")
                            # Use original file as fallback
                            try:
                                new_zip.writestr(drawing_path, original_zip.read(drawing_path))
                            except Exception as fallback_e:
                                app_logger.error(f"Failed to copy original drawing file as fallback: {fallback_e}")
                    
                    # Process data file
                    if data_path in original_zip.namelist():
                        try:
                            data_xml = original_zip.read(data_path)
                            data_tree = etree.fromstring(data_xml)
                            
                            for item in items:
                                count = str(item['count_src'])
                                translated_text = translations.get(count)
                                
                                if not translated_text:
                                    continue
                                
                                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                                original_text = item.get('original_text', '')
                                
                                # Find all dgm:pt elements that contain text
                                points = data_tree.xpath('.//dgm:pt[.//a:t]', namespaces=namespaces)
                                
                                # Try to find matching text by content
                                for point in points:
                                    point_paragraphs = point.xpath('.//a:p', namespaces=namespaces)
                                    for p_idx, point_paragraph in enumerate(point_paragraphs):
                                        # Get current text from this paragraph
                                        point_text_runs = point_paragraph.xpath('.//a:r', namespaces=namespaces)
                                        if point_text_runs:
                                            point_run_info = _process_excel_smartart_text_runs(point_text_runs, namespaces)
                                            # If the original text matches, update this paragraph
                                            if point_run_info['merged_text'].strip() == original_text.strip():
                                                _distribute_excel_smartart_text_to_runs(point_paragraph, translated_text, item, namespaces)
                                                app_logger.info(f"Updated Excel SmartArt data text for diagram {diagram_index}, count_src {count}: '{original_text}' -> '{translated_text[:50]}...'")
                                                break
                            
                            # Write modified data
                            modified_data_xml = etree.tostring(data_tree, xml_declaration=True, 
                                                              encoding="UTF-8", standalone="yes")
                            new_zip.writestr(data_path, modified_data_xml)
                            app_logger.info(f"Saved modified Excel SmartArt data file: {data_path}")
                            
                        except Exception as e:
                            app_logger.error(f"Failed to apply Excel SmartArt translation to {data_path}: {e}")
                            # Use original file as fallback
                            try:
                                new_zip.writestr(data_path, original_zip.read(data_path))
                            except Exception as fallback_e:
                                app_logger.error(f"Failed to copy original data file as fallback: {fallback_e}")
        
        # Replace original file with modified file
        shutil.move(temp_excel_path, file_path)
        app_logger.info(f"Excel SmartArt translations applied successfully")
        return file_path
        
    except Exception as e:
        app_logger.error(f"Failed to apply Excel SmartArt translations: {e}")
        # Clean up temporary file if it exists
        if os.path.exists(temp_excel_path):
            try:
                os.remove(temp_excel_path)
            except:
                pass
        return file_path


def _distribute_excel_smartart_text_to_runs(parent_element, translated_text: str, item: Dict, namespaces: Dict):
    """Distribute translated text across multiple runs in Excel SmartArt, preserving spacing and structure."""
    text_runs = parent_element.xpath('.//a:r', namespaces=namespaces)
    
    if not text_runs:
        return
    
    original_run_texts = item.get('run_texts', [])
    original_run_lengths = item.get('run_lengths', [])
    
    # If we don't have the original structure, fallback to simple distribution
    if not original_run_texts or len(original_run_texts) != len(text_runs):
        app_logger.warning(f"Mismatch in Excel SmartArt run structure, using simple distribution")
        _simple_excel_smartart_text_distribution(text_runs, translated_text, namespaces)
        return
    
    # Use intelligent distribution based on original structure
    _intelligent_excel_smartart_text_distribution(text_runs, translated_text, original_run_texts, original_run_lengths, namespaces)


def _simple_excel_smartart_text_distribution(text_runs, translated_text: str, namespaces: Dict):
    """Simple fallback distribution method for Excel SmartArt."""
    if not text_runs:
        return
    
    # Put all translated text in the first run, clear others
    for i, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if text_node:
            if i == 0:
                text_node[0].text = translated_text
            else:
                text_node[0].text = ""


def _intelligent_excel_smartart_text_distribution(text_runs, translated_text: str, original_run_texts: List[str], 
                                                 original_run_lengths: List[int], namespaces: Dict):
    """Intelligent text distribution for Excel SmartArt that preserves spacing and structure."""
    
    # Calculate total length excluding empty runs
    meaningful_runs = [(i, length) for i, length in enumerate(original_run_lengths) if length > 0]
    total_meaningful_length = sum(length for _, length in meaningful_runs)
    
    if total_meaningful_length == 0:
        _simple_excel_smartart_text_distribution(text_runs, translated_text, namespaces)
        return
    
    # Handle special cases for spacing
    translated_chars = list(translated_text)
    char_index = 0
    
    for run_index, text_run in enumerate(text_runs):
        text_node = text_run.xpath('./a:t', namespaces=namespaces)
        if not text_node:
            continue
            
        original_text = original_run_texts[run_index] if run_index < len(original_run_texts) else ""
        original_length = original_run_lengths[run_index] if run_index < len(original_run_lengths) else 0
        
        # Handle empty or whitespace-only runs
        if original_length == 0 or not original_text.strip():
            # If original run was empty or whitespace, keep it empty
            # unless it was purely whitespace and we need to preserve spacing
            if original_text and not original_text.strip():
                # This run contained only whitespace, try to preserve some spacing
                if char_index < len(translated_chars) and translated_chars[char_index] == ' ':
                    text_node[0].text = ' '
                    char_index += 1
                else:
                    text_node[0].text = ""
            else:
                text_node[0].text = ""
            continue
        
        # Calculate how much text this run should get
        if run_index == len(text_runs) - 1:
            # Last meaningful run gets all remaining text
            remaining_text = ''.join(translated_chars[char_index:])
            text_node[0].text = remaining_text
        else:
            # Calculate proportional distribution
            proportion = original_length / total_meaningful_length
            target_length = max(1, int(len(translated_text) * proportion))
            
            # Try to break at word boundaries
            run_text = ""
            chars_taken = 0
            
            while chars_taken < target_length and char_index < len(translated_chars):
                char = translated_chars[char_index]
                run_text += char
                chars_taken += 1
                char_index += 1
                
                # If we've reached the target length, try to extend to a word boundary
                if chars_taken >= target_length and char_index < len(translated_chars):
                    # Look ahead for word boundary
                    if char != ' ' and translated_chars[char_index] != ' ':
                        # Continue until we find a space or reach the end
                        while (char_index < len(translated_chars) and 
                               translated_chars[char_index] != ' ' and 
                               chars_taken < target_length * 1.5):  # Don't go too far
                            char = translated_chars[char_index]
                            run_text += char
                            chars_taken += 1
                            char_index += 1
                    break
            
            text_node[0].text = run_text
