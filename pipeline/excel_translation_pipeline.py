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

        sheets = list(wb.sheets)
        for sheet in sheets:
            sheet_name = sheet.name
            if should_translate(sheet_name):
                count += 1
                cell_data.append({
                    "count": count,
                    "sheet": sheet_name,
                    "value": sheet_name,
                    "type": "sheet_name"
                })
        
        def process_sheet(sheet):
            nonlocal count
            sheet_data = []
            
            # Process cells
            used_range = sheet.used_range
            if used_range:
                all_values = used_range.options(ndim=2).value
                all_addresses = [[sheet.cells(row_idx, col_idx) for col_idx in range(1, used_range.last_cell.column + 1)] 
                               for row_idx in range(1, used_range.last_cell.row + 1)]
                
                for row_idx, row_values in enumerate(all_values):
                    for col_idx, cell_value in enumerate(row_values):
                        if cell_value is None or isinstance(cell_value, datetime) or not should_translate(str(cell_value) if cell_value is not None else ""):
                            continue
                        
                        if isinstance(cell_value, datetime):
                            cell_value = cell_value.isoformat()
                        else:
                            cell_value = str(cell_value).replace("\n", "␊").replace("\r", "␍")
                        
                        cell = all_addresses[row_idx][col_idx]
                        is_merged = False
                        
                        if cell.api.MergeCells:
                            merge_area = cell.api.MergeArea
                            if cell.row == merge_area.Row and cell.column == merge_area.Column:
                                is_merged = True
                            else:
                                continue
                        
                        sheet_data.append({
                            "count": 0,
                            "sheet": sheet.name,
                            "row": row_idx + 1,
                            "column": col_idx + 1,
                            "value": cell_value,
                            "is_merged": is_merged,
                            "type": "cell"
                        })
            
            # Process shapes - with safe group handling
            try:
                shapes = list(sheet.shapes)  # Create a copy
                if shapes:
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
                                                    "count": 0,
                                                    "sheet": sheet.name,
                                                    "shape_name": child_name,
                                                    "unique_shape_id": unique_shape_id,
                                                    "shape_index": -1,  # Negative indicates group child
                                                    "group_name": group_name,
                                                    "group_index": group_index,
                                                    "child_path": item_path,  # Path to locate nested items
                                                    "value": text_value,
                                                    "type": "group_textbox"
                                                })
                                    except Exception as child_error:
                                        app_logger.warning(f"Error processing group child {path}/{i}: {str(child_error)}")
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
                                group_name = shape.name
                                group_items_data = process_group_items(shape, shape_idx, group_name)
                                sheet_data.extend(group_items_data)
                            else:
                                # Process individual shape
                                if hasattr(shape, 'text') and shape.text:
                                    text_value = shape.text
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
                                        "count": 0,
                                        "sheet": sheet.name,
                                        "shape_name": original_shape_name,
                                        "unique_shape_id": unique_shape_id,
                                        "shape_index": shape_idx,
                                        "value": text_value,
                                        "type": "textbox"
                                    })
                        except Exception as e:
                            app_logger.warning(f"Error processing shape #{shape_idx}: {str(e)}")
                    
            except Exception as e:
                app_logger.warning(f"Error processing shapes in sheet {sheet.name}: {str(e)}")
                
            return sheet_data
            
        results = []
        for sheet in sheets:
            sheet_data = process_sheet(sheet)
            results.append(sheet_data)
            
        for sheet_data in results:
            for item in sheet_data:
                count += 1
                item["count"] = count
                cell_data.append(item)
                
    finally:
        wb.close()
        app.quit()
    
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(cell_data, json_file, ensure_ascii=False, indent=4)

    return json_path


def write_translated_content_to_excel(file_path, original_json_path, translated_json_path):
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)

    translations = {str(item["count"]): item["translated"] for item in translated_data}
    
    sheet_name_translations = {}
    for cell_info in original_data:
        if cell_info.get("type") == "sheet_name":
            count = str(cell_info["count"])
            original_sheet_name = cell_info["sheet"]
            translated_sheet_name = translations.get(count)
            if translated_sheet_name:
                sheet_name_translations[original_sheet_name] = translated_sheet_name.replace("␊", "\n").replace("␍", "\r")

    sheets_data = {}
    for cell_info in original_data:
        if cell_info.get("type") == "sheet_name":
            continue
            
        count = str(cell_info["count"])
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
                "value": translated_value
            })
        else:
            sheets_data[sheet_name]["textboxes"].append(cell_info.copy())
            sheets_data[sheet_name]["textboxes"][-1]["value"] = translated_value
    
    app = xw.App(visible=False)
    app.screen_updating = False
    app.display_alerts = False
    
    try:
        wb = app.books.open(file_path)
        
        original_to_translated_sheet_map = {}
        new_sheet_names = []
        
        for sheet_name, data in sheets_data.items():
            translated_sheet_name = sheet_name_translations.get(sheet_name)
            if translated_sheet_name:
                original_to_translated_sheet_map[sheet_name] = translated_sheet_name
                new_sheet_names.append((sheet_name, translated_sheet_name))
        
        existing_names = set(sheet.name for sheet in wb.sheets)
        temp_names = {}
        
        for original_name, new_name in new_sheet_names:
            if new_name in existing_names and new_name != original_name:
                temp_name = f"temp_{original_name}_{hash(original_name) % 10000}"
                temp_names[original_name] = temp_name
        
        for original_name, temp_name in temp_names.items():
            wb.sheets[original_name].name = temp_name
        
        for original_name, new_name in new_sheet_names:
            actual_original_name = temp_names.get(original_name, original_name)
            try:
                wb.sheets[actual_original_name].name = new_name
            except Exception as e:
                app_logger.warning(f"Error renaming sheet '{original_name}' to '{new_name}': {str(e)}")

        updated_sheets_data = {}
        for sheet_name, data in sheets_data.items():
            actual_sheet_name = sheet_name_translations.get(sheet_name, sheet_name)
            updated_sheets_data[actual_sheet_name] = data
        
        for sheet_name, data in updated_sheets_data.items():
            try:
                sheet = wb.sheets[sheet_name]
                
                # Process cells
                cells_by_row = {}
                for cell in data["cells"]:
                    row = cell["row"]
                    if row not in cells_by_row:
                        cells_by_row[row] = []
                    cells_by_row[row].append(cell)
                
                for row, cells in cells_by_row.items():
                    if len(cells) > 5:
                        min_col = min(c["column"] for c in cells)
                        max_col = max(c["column"] for c in cells)
                        
                        current_values = sheet.range((row, min_col), (row, max_col)).value
                        if not isinstance(current_values, list):
                            current_values = [current_values]
                        
                        new_values = list(current_values)
                        for cell in cells:
                            col_idx = cell["column"] - min_col
                            if col_idx < len(new_values):
                                new_values[col_idx] = cell["value"]
                        
                        sheet.range((row, min_col), (row, min_col + len(new_values) - 1)).value = new_values
                    else:
                        for cell in cells:
                            sheet.cells(cell["row"], cell["column"]).value = cell["value"]
                
                # Get all shapes in the sheet
                all_shapes = list(sheet.shapes)
                
                # Split textboxes by type
                normal_textboxes = [tb for tb in data["textboxes"] if tb.get("type") == "textbox"]
                group_textboxes = [tb for tb in data["textboxes"] if tb.get("type") == "group_textbox"]
                
                # Process normal textboxes
                for textbox in normal_textboxes:
                    matched = False
                    shape_index = textbox.get("shape_index")
                    
                    # Method 1: Find by index
                    if shape_index is not None and 0 <= shape_index < len(all_shapes):
                        try:
                            shape = all_shapes[shape_index]
                            if hasattr(shape, 'text'):
                                shape.text = textbox["value"]
                                matched = True
                        except Exception as e:
                            app_logger.warning(f"Error updating shape by index {shape_index}: {str(e)}")
                    
                    # Method 2: Find by unique ID
                    if not matched and textbox.get("unique_shape_id"):
                        original_name = textbox["shape_name"]
                        same_name_shapes = [s for s in all_shapes if s.name == original_name]
                        unique_id_parts = textbox["unique_shape_id"].split("_")
                        if len(unique_id_parts) > 1:
                            try:
                                id_number = int(unique_id_parts[-1])
                                if 1 <= id_number <= len(same_name_shapes):
                                    shape = same_name_shapes[id_number - 1]
                                    if hasattr(shape, 'text'):
                                        shape.text = textbox["value"]
                                        matched = True
                                        app_logger.info(f"Updated shape by unique ID: {textbox['unique_shape_id']}")
                            except (ValueError, IndexError) as e:
                                app_logger.warning(f"Error updating shape with unique ID {textbox['unique_shape_id']}: {str(e)}")
                    
                    # Method 3: Find by name
                    if not matched:
                        try:
                            app_logger.warning(f"Falling back to shape name lookup for {textbox['shape_name']}")
                            for shape in all_shapes:
                                if shape.name == textbox["shape_name"]:
                                    shape.text = textbox["value"]
                                    app_logger.info(f"Updated shape by name: {textbox['shape_name']}")
                                    break
                        except Exception as e:
                            app_logger.warning(f"Error updating text box {textbox['shape_name']} in sheet {sheet_name}: {str(e)}")
                
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
                
            except Exception as e:
                app_logger.warning(f"Error processing sheet {sheet_name}: {str(e)}")
        
        result_folder = os.path.join('result')
        os.makedirs(result_folder, exist_ok=True)
        
        result_path = os.path.join(
            result_folder,
            f"{os.path.splitext(os.path.basename(file_path))[0]}_translated{os.path.splitext(file_path)[1]}"
        )
        
        wb.save(result_path)
        app_logger.info(f"Translated Excel saved to: {result_path}")
        
    finally:
        wb.close()
        app.quit()
        
    return result_path