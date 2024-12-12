import json
import os
from lxml import etree
from zipfile import ZipFile
from .skip_pipeline import should_translate

def extract_word_content_to_json(file_path):
    """
    Extract all text content from a Word document (DOCX) using XML parsing, including paragraphs and tables.
    """
    # Open the Word document as a ZIP archive
    with ZipFile(file_path, 'r') as docx:
        document_xml = docx.read('word/document.xml')

    # Parse the main document XML
    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    document_tree = etree.fromstring(document_xml)

    content_data = []
    count = 0

    def extract_text_nodes(tree):
        """Extract all text nodes from the XML tree."""
        nonlocal count
        text_nodes = tree.xpath('.//w:t', namespaces=namespaces)
        for text_node in text_nodes:
            count += 1
            text_value = text_node.text.strip() if text_node.text else ""
            text_value = text_value.replace("\n", "␊").replace("\r", "␍")
            if should_translate(text_value):
                content_data.append({
                    "count": count,
                    "type": "text",
                    "value": text_value
                })

    # Extract text content
    extract_text_nodes(document_tree)

    # Save content to JSON
    temp_folder = "temp"
    os.makedirs(temp_folder, exist_ok=True)
    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)

    return json_path

def modify_json(data_list):
    combined_data = {}
    for entry in data_list:
        if entry.startswith("```json"):
            entry = entry[len("```json"):].strip()
        if entry.endswith("```"):
            entry = entry[:-len("```")].strip()
        
        try:
            json_data = json.loads(entry)
            if isinstance(json_data, dict):
                combined_data.update(json_data)
        except json.JSONDecodeError:
            print(f"Warning: Skipping invalid JSON entry: {entry}")
            continue
    
    return combined_data

def write_translated_content_to_word(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to the Word document while preserving the format and structure.
    """
    # Open the Word document as a ZIP archive
    with ZipFile(file_path, 'r') as docx:
        # Read main document XML as bytes
        document_xml = docx.read('word/document.xml')

    # Parse the main document XML
    namespaces = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    document_tree = etree.fromstring(document_xml)

    # Load original JSON
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)

    # Load translated JSON
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_raw = json.load(translated_file)
        translated_data = modify_json(translated_raw)

    # Create a mapping of translations
    translations = {str(key): value for key, value in translated_data.items()}

    # Replace text in paragraphs and tables
    count = 0

    def replace_text_in_tree(tree):
        """Replace text in XML tree while preserving all formatting."""
        nonlocal count
        # Find all text nodes in the document
        text_nodes = tree.xpath('.//w:t', namespaces=namespaces)
        for text_node in text_nodes:
            count += 1
            text_value = text_node.text.strip() if text_node.text else ""
            if should_translate(text_value):  # Only replace translatable content
                # Check if there is a translation for this count
                translated_text = translations.get(str(count), None)
                translated_text = translated_text.replace("␊", "\n").replace("␍", "\r")
                if translated_text:
                    # Replace text without altering any formatting
                    # print(f"Replacing text: '{text_node.text}' -> '{translated_text}'")
                    text_node.text = translated_text

    # Replace text in the main document tree
    replace_text_in_tree(document_tree)

    # Save the modified XML back to the Word document
    temp_folder = "temp"
    os.makedirs(temp_folder, exist_ok=True)
    temp_word_folder = os.path.join(temp_folder, "word")
    os.makedirs(temp_word_folder, exist_ok=True)
    modified_doc_path = os.path.join(temp_word_folder, "document.xml")

    with open(modified_doc_path, "wb") as modified_doc:
        modified_doc.write(etree.tostring(document_tree, xml_declaration=True, encoding="UTF-8", standalone="yes"))

    # Create a new Word document with the modified content
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    result_path = os.path.join(result_folder, f"{os.path.splitext(os.path.basename(file_path))[0]}_translated.docx")

    with ZipFile(file_path, 'r') as original_doc:
        with ZipFile(result_path, 'w') as new_doc:
            # Copy all files from the original Word document to the new document
            for item in original_doc.infolist():
                if item.filename != 'word/document.xml':
                    new_doc.writestr(item, original_doc.read(item.filename))
            # Write the modified document.xml
            new_doc.write(modified_doc_path, 'word/document.xml')

    return result_path


if __name__ == "__main__":
    file_path = "test.docx"
    
    # Extract content to JSON
    extracted_json_path = extract_word_content_to_json(file_path)
    print(f"Content extracted to: {extracted_json_path}")
    
    # Simulate translated JSON (for testing)
    translated_json_path = "temp/word_translated.json"
    with open(extracted_json_path, "r", encoding="utf-8") as f:
        content = json.load(f)
    for item in content:
        item["text"] = f"[Translated] {item['text']}"
    with open(translated_json_path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=4)

    # Write back translated content
    translated_file_path = write_translated_content_to_word(file_path, extracted_json_path, translated_json_path)
    print(f"Translated document saved to: {translated_file_path}")
