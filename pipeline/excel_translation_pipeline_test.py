# pipeline/excel_translation_pipeline_test.py
import os
import json
import re
import tempfile
import shutil
from datetime import datetime
from zipfile import ZipFile
from lxml import etree
from typing import Dict, List, Any
import xlwings as xw
from .skip_pipeline import should_translate
from config.log_config import app_logger


def extract_excel_content_to_json(file_path):
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
        
        def process_sheet(sheet):
            nonlocal count
            sheet_data = []
            
            # Process cells - improved logic
            try:
                used_range = sheet.used_range
                if used_range:
                    app_logger.info(f"Processing sheet '{sheet.name}' with range: {used_range.address}")
                    
                    # Get all values and process row by row
                    max_row = used_range.last_cell.row
                    max_col = used_range.last_cell.column
                    
                    # Process each cell individually to avoid batch issues
                    for row_idx in range(1, max_row + 1):
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
                                
                                # Skip formula cells (cells that start with '=')
                                if isinstance(cell_value, str) and cell_value.strip().startswith('='):
                                    continue
                                
                                # Skip cells that shouldn't be translated
                                if not should_translate(str(cell_value)):
                                    continue
                                
                                # Handle merged cells - improved logic
                                is_merged = False
                                should_process = True
                                
                                try:
                                    if cell.api.MergeCells:
                                        merge_area = cell.api.MergeArea
                                        # Only process the top-left cell of merged area
                                        if cell.row == merge_area.Row and cell.column == merge_area.Column:
                                            is_merged = True
                                            should_process = True
                                        else:
                                            # This is a merged cell but not the top-left one
                                            should_process = False
                                except Exception as merge_error:
                                    app_logger.warning(f"Error checking merge status for cell {cell.address}: {str(merge_error)}")
                                    # If we can't determine merge status, process the cell anyway
                                    should_process = True
                                
                                if not should_process:
                                    continue
                                
                                # Process valid cell value
                                if isinstance(cell_value, datetime):
                                    cell_value = cell_value.isoformat()
                                else:
                                    cell_value = str(cell_value).replace("\n", "␊").replace("\r", "␍")
                                
                                sheet_data.append({
                                    "count_src": 0,  # Will be updated later
                                    "sheet": sheet.name,
                                    "row": row_idx,
                                    "column": col_idx,
                                    "value": cell_value,
                                    "is_merged": is_merged,
                                    "type": "cell"
                                })
                                
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
                                                
                                                # Add to group items data
                                                group_items_data.append({
                                                    "count_src": 0,
                                                    "sheet": sheet.name,
                                                    "shape_name": child_name,
                                                    "unique_shape_id": unique_shape_id,
                                                    "shape_index": -1,  # Negative indicates group child
                                                    "group_name": group_name,
                                                    "group_index": group_index,
                                                    "child_path": item_path,
                                                    "value": text_value,
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
                                        
                                        sheet_data.append({
                                            "count_src": 0,
                                            "sheet": sheet.name,
                                            "shape_name": original_shape_name,
                                            "unique_shape_id": unique_shape_id,
                                            "shape_index": shape_idx,
                                            "value": text_value,
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
        results = []
        for sheet in sheets:
            try:
                sheet_data = process_sheet(sheet)
                results.append(sheet_data)
            except Exception as e:
                app_logger.error(f"Error processing sheet {sheet.name}: {str(e)}")
                # Continue with next sheet
                results.append([])
        
        # Assign count_src values
        for sheet_data in results:
            for item in sheet_data:
                count += 1
                item["count_src"] = count
                cell_data.append(item)
                
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
    
    # Save to JSON
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(cell_data, json_file, ensure_ascii=False, indent=4)

    app_logger.info(f"Total extracted items: {len(cell_data)}")
    return json_path


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


def write_translated_content_to_excel(file_path, original_json_path, translated_json_path):
    # Load JSON data
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    # Create translation dictionary
    translations = {str(item["count_src"]): item["translated"] for item in translated_data}
    
    # Collect sheet name translations
    sheet_name_translations = {}
    for cell_info in original_data:
        if cell_info.get("type") == "sheet_name":
            count = str(cell_info["count_src"])
            original_sheet_name = cell_info["sheet"]
            translated_sheet_name = translations.get(count)
            if translated_sheet_name:
                # Sanitize the sheet name to avoid invalid characters
                sanitized_name = sanitize_sheet_name(translated_sheet_name.replace("␊", "\n").replace("␍", "\r"))
                sheet_name_translations[original_sheet_name] = sanitized_name
                
                # Log if the name was changed
                if sanitized_name != translated_sheet_name.replace("␊", "\n").replace("␍", "\r"):
                    app_logger.warning(f"Sheet name '{translated_sheet_name}' contains invalid characters and was changed to '{sanitized_name}'")

    # Organize data by sheet (excluding SmartArt items)
    sheets_data = {}
    smartart_items = []
    
    for cell_info in original_data:
        if cell_info.get("type") == "sheet_name":
            continue
        elif cell_info.get("type") == "excel_smartart":
            smartart_items.append(cell_info)
            continue
            
        count = str(cell_info["count_src"])
        sheet_name = cell_info["sheet"]
        
        if sheet_name not in sheets_data:
            sheets_data[sheet_name] = {
                "cells": [],
                "textboxes": []
            }
            
        translated_value = translations.get(count, None)
        if translated_value is None:
            app_logger.warning(
                f"Translation missing for count {count}. Original text: '{cell_info['value']}'"
            )
            continue
            
        translated_value = translated_value.replace("␊", "\n").replace("␍", "\r")
        
        if cell_info.get("type") == "cell":
            sheets_data[sheet_name]["cells"].append({
                "row": cell_info["row"],
                "column": cell_info["column"],
                "value": translated_value,
                "is_merged": cell_info.get("is_merged", False)
            })
        else:
            sheets_data[sheet_name]["textboxes"].append(cell_info.copy())
            sheets_data[sheet_name]["textboxes"][-1]["value"] = translated_value
    
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
                
                # Process cells - improved approach
                app_logger.info(f"Processing {len(data['cells'])} cells in sheet '{sheet_name}'")
                
                # Simple approach: update each cell individually
                for cell_data in data["cells"]:
                    try:
                        row = cell_data["row"]
                        column = cell_data["column"]
                        value = cell_data["value"]
                        
                        # Update the cell
                        sheet.cells(row, column).value = value
                        
                    except Exception as cell_error:
                        app_logger.warning(f"Error updating cell ({row}, {column}) in sheet '{sheet_name}': {str(cell_error)}")
                        continue
                
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
                
                # Process normal textboxes
                for textbox in normal_textboxes:
                    try:
                        matched = False
                        shape_index = textbox.get("shape_index")
                        
                        # Method 1: Find by index
                        if shape_index is not None and 0 <= shape_index < len(all_shapes):
                            try:
                                shape = all_shapes[shape_index]
                                if hasattr(shape, 'text'):
                                    shape.text = textbox["value"]
                                    matched = True
                                    app_logger.info(f"Updated textbox by index {shape_index}")
                            except Exception as e:
                                app_logger.warning(f"Error updating shape by index {shape_index}: {str(e)}")
                        
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
                                            app_logger.info(f"Updated shape by unique ID: {textbox['unique_shape_id']}")
                            except Exception as e:
                                app_logger.warning(f"Error updating shape with unique ID {textbox['unique_shape_id']}: {str(e)}")
                        
                        # Method 3: Find by name
                        if not matched:
                            try:
                                for shape in all_shapes:
                                    if shape.name == textbox["shape_name"]:
                                        if hasattr(shape, 'text'):
                                            shape.text = textbox["value"]
                                            matched = True
                                            app_logger.info(f"Updated shape by name: {textbox['shape_name']}")
                                            break
                            except Exception as e:
                                app_logger.warning(f"Error updating shape by name {textbox['shape_name']}: {str(e)}")
                        
                        if not matched:
                            app_logger.warning(f"Could not find shape to update: {textbox.get('shape_name', 'unknown')}")
                            
                    except Exception as textbox_error:
                        app_logger.warning(f"Error processing textbox: {str(textbox_error)}")
                        continue
                
                # Process group textboxes with nested path support
                for textbox in group_textboxes:
                    try:
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
                                        app_logger.info(f"Updated group '{group_name}' child with path {child_path} using TextFrame")
                                except:
                                    pass
                                
                                # Method 2: TextFrame2
                                if not updated:
                                    try:
                                        if hasattr(child_item, 'TextFrame2'):
                                            child_item.TextFrame2.TextRange.Text = textbox["value"]
                                            updated = True
                                            app_logger.info(f"Updated group '{group_name}' child with path {child_path} using TextFrame2")
                                    except:
                                        pass
                                
                                if not updated:
                                    app_logger.warning(f"Could not update group '{group_name}' child with path {child_path}")
                            else:
                                app_logger.warning(f"Could not navigate to child with path {child_path} in group '{group_name}'")
                        else:
                            app_logger.warning(f"Could not find group '{group_name}' or it lacks GroupItems")
                    except Exception as e:
                        app_logger.warning(f"Error processing group shape, group: {textbox.get('group_name')}, path: {textbox.get('child_path')}: {str(e)}")
                        continue
                
            except Exception as e:
                app_logger.error(f"Error processing sheet {sheet_name}: {str(e)}")
                continue
        
        # Save the workbook first
        result_folder = os.path.join('result')
        os.makedirs(result_folder, exist_ok=True)
        
        result_path = os.path.join(
            result_folder,
            f"{os.path.splitext(os.path.basename(file_path))[0]}_translated{os.path.splitext(file_path)[1]}"
        )
        
        try:
            wb.save(result_path)
            app_logger.info(f"Translated Excel (without SmartArt) saved to: {result_path}")
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
    
    return result_path


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
                                            app_logger.info(f"Updated Excel SmartArt drawing text for diagram {diagram_index}, shape {item['shape_index']}")
                            
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
                                                app_logger.info(f"Updated Excel SmartArt data text for diagram {diagram_index}: '{original_text}' -> '{translated_text[:50]}...'")
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
