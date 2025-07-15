# pipeline/excel_translation_pipeline_test.py
import os
import json
from datetime import datetime
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
                
        app_logger.info(f"Total extracted items: {len(cell_data)}")
                
    finally:
        try:
            wb.close()
            app.quit()
        except Exception as e:
            app_logger.error(f"Error closing workbook: {str(e)}")
    
    # Save to JSON
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(cell_data, json_file, ensure_ascii=False, indent=4)

    return json_path


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

    # Organize data by sheet
    sheets_data = {}
    for cell_info in original_data:
        if cell_info.get("type") == "sheet_name":
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
        
        # Save the workbook
        result_folder = os.path.join('result')
        os.makedirs(result_folder, exist_ok=True)
        
        result_path = os.path.join(
            result_folder,
            f"{os.path.splitext(os.path.basename(file_path))[0]}_translated{os.path.splitext(file_path)[1]}"
        )
        
        try:
            wb.save(result_path)
            app_logger.info(f"Translated Excel saved to: {result_path}")
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
            
    return result_path