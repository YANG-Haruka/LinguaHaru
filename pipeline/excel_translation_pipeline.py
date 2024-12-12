import os
import re
import json
from lxml import etree
from zipfile import ZipFile
from skip_pipeline import should_translate


def extract_excel_content_to_json(file_path):
    """
    Extract all text content from an Excel file (XLSX) using XML parsing.
    """
    content_data = []
    count = 0

    with ZipFile(file_path, 'r') as xlsx:
        # Read shared strings
        shared_strings_xml = xlsx.read('xl/sharedStrings.xml')
        shared_strings_tree = etree.fromstring(shared_strings_xml)
        shared_strings = [t.text for t in shared_strings_tree.findall('.//t')]

        # Parse sheets
        sheet_files = [f for f in xlsx.namelist() if f.startswith('xl/worksheets/sheet') and f.endswith('.xml')]

        for sheet_index, sheet_file in enumerate(sheet_files, start=1):
            sheet_xml = xlsx.read(sheet_file)
            sheet_tree = etree.fromstring(sheet_xml)

            # Find all cells
            rows = sheet_tree.findall('.//row')
            for row in rows:
                for cell in row.findall('.//c'):
                    cell_value = None
                    if 't' in cell.attrib and cell.attrib['t'] == 's':  # Shared string
                        string_index = int(cell.find('v').text)
                        cell_value = shared_strings[string_index]
                    elif cell.find('v') is not None:  # Inline value
                        cell_value = cell.find('v').text

                    if cell_value is not None and should_translate(cell_value):
                        count += 1
                        content_data.append({
                            "count": count,
                            "sheet_index": sheet_index,
                            "row": int(row.attrib.get('r', 0)),
                            "column": cell.attrib.get('r'),
                            "value": cell_value.strip(),
                        })

    # Save content to JSON
    temp_folder = "temp"
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)

    return json_path

def modify_json(data_list):
    cleaned_json_objects = []
    for entry in data_list:
        if not entry.startswith("```json\n"):
            entry = "```json\n" + entry
        if not entry.endswith("\n```"):
            entry = entry + "\n```"
        cleaned_json_objects.append(entry)
    return cleaned_json_objects

def write_translated_content_to_excel(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to the Excel file while preserving the format and structure.
    """
    # Load original and translated JSON
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)
        translated_data = modify_json(translated_data)

    translations = {str(item['count']): item['value'] for item in translated_data}

    with ZipFile(file_path, 'r') as xlsx:
        # Prepare a temporary folder for modifications
        temp_folder = "temp"
        os.makedirs(temp_folder, exist_ok=True)

        # Copy the original files
        for item in xlsx.namelist():
            with open(os.path.join(temp_folder, item), 'wb') as f:
                f.write(xlsx.read(item))

        # Update sharedStrings.xml
        shared_strings_path = os.path.join(temp_folder, 'xl/sharedStrings.xml')
        with open(shared_strings_path, 'rb') as f:
            shared_strings_tree = etree.parse(f)

        shared_strings = shared_strings_tree.findall('.//t')
        for item in original_data:
            count = str(item['count'])
            if count in translations:
                string_index = int(item['value'])
                shared_strings[string_index].text = translations[count]

        with open(shared_strings_path, 'wb') as f:
            f.write(etree.tostring(shared_strings_tree, pretty_print=True))

        # Save the modified file
        result_path = os.path.join("result", f"{os.path.splitext(file_path)[0]}_translated.xlsx")
        with ZipFile(result_path, 'w') as result_xlsx:
            for root, dirs, files in os.walk(temp_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, temp_folder)
                    result_xlsx.write(file_path, arcname)

    return result_path


if __name__=="__main__":
    file_path = "test\PJ Tower List with Price with color code.xlsx"
    original_json_path = "temp\src.json"
    translated_json_path = "temp\dst_translated.json"
    write_translated_content_to_excel(file_path, original_json_path, translated_json_path)