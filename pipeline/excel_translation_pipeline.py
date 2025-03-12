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
        
        def process_sheet(sheet):
            nonlocal count
            sheet_data = []
            
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
            
            try:
                shapes = sheet.shapes
                if shapes:
                    for shape in shapes:
                        if hasattr(shape, 'text') and shape.text:
                            text_value = shape.text
                            if not should_translate(text_value):
                                continue
                                
                            text_value = str(text_value).replace("\n", "␊").replace("\r", "␍")
                            
                            sheet_data.append({
                                "count": 0,
                                "sheet": sheet.name,
                                "shape_name": shape.name,
                                "value": text_value,
                                "type": "textbox"
                            })
            except Exception as e:
                app_logger.warning(f"Error processing shapes in sheet {sheet.name}: {str(e)}")
                
            return sheet_data
            
        sheets = list(wb.sheets)
        
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
    
    sheets_data = {}
    for cell_info in original_data:
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
        
        if cell_info.get("type", "cell") == "cell":
            sheets_data[sheet_name]["cells"].append({
                "row": cell_info["row"],
                "column": cell_info["column"],
                "value": translated_value
            })
        else:
            sheets_data[sheet_name]["textboxes"].append({
                "shape_name": cell_info["shape_name"],
                "value": translated_value
            })
    
    app = xw.App(visible=False)
    app.screen_updating = False
    app.display_alerts = False
    
    try:
        wb = app.books.open(file_path)
        
        for sheet_name, data in sheets_data.items():
            sheet = wb.sheets[sheet_name]
            
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
            
            for textbox in data["textboxes"]:
                try:
                    for shape in sheet.shapes:
                        if shape.name == textbox["shape_name"]:
                            shape.text = textbox["value"]
                            break
                except Exception as e:
                    app_logger.warning(f"Error updating text box {textbox['shape_name']} in sheet {sheet_name}: {str(e)}")
        
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