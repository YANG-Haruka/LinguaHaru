import json
import os
import zipfile
from bs4 import BeautifulSoup
from .skip_pipeline import should_translate
from config.log_config import app_logger

def extract_epub_content_to_json(file_path):
    """
    Extract text content from EPUB file and save in JSON format.
    Preserves HTML structure while extracting translatable text.
    """
    content_data = []
    count = 0
    
    # Create temp directory
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join("temp", filename)

    os.makedirs(temp_folder, exist_ok=True)
    
    # EPUB files are zip files containing HTML/XHTML
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        # First, extract the OPF file to find content documents
        content_files = []
        opf_file = None
        
        # Look for the container.xml to find the OPF file
        if 'META-INF/container.xml' in zip_ref.namelist():
            with zip_ref.open('META-INF/container.xml') as container_file:
                container_soup = BeautifulSoup(container_file.read(), 'xml')
                rootfile_elem = container_soup.find('rootfile')
                if rootfile_elem and 'full-path' in rootfile_elem.attrs:
                    opf_file = rootfile_elem['full-path']
        
        # If OPF file was found, parse it to get the content files
        if opf_file:
            with zip_ref.open(opf_file) as opf_content:
                opf_soup = BeautifulSoup(opf_content.read(), 'xml')
                
                # Find the manifest
                manifest = opf_soup.find('manifest')
                if manifest:
                    # Get all items that are HTML/XHTML content
                    for item in manifest.find_all('item'):
                        if ('media-type' in item.attrs and 
                            item['media-type'] in ['application/xhtml+xml', 'text/html']):
                            # Get the path to the content file
                            href = item['href']
                            # Combine with OPF path if needed
                            if '/' in opf_file:
                                base_dir = os.path.dirname(opf_file)
                                href = os.path.join(base_dir, href)
                            content_files.append(href)
        
        # If no content files found via OPF, fallback to looking for HTML/XHTML files
        if not content_files:
            content_files = [f for f in zip_ref.namelist() 
                            if f.endswith('.html') or f.endswith('.xhtml') or f.endswith('.htm')]
            
        app_logger.info(f"Found {len(content_files)} content files in EPUB")
        
        # Process each content file
        for content_file in content_files:
            # Skip if file doesn't exist in the zip (safety check)
            if content_file not in zip_ref.namelist():
                app_logger.warning(f"Content file {content_file} not found in EPUB")
                continue
                
            try:
                with zip_ref.open(content_file) as html_file:
                    html_content = html_file.read().decode('utf-8', errors='replace')
                    
                    # Save original content for reference
                    file_name = os.path.basename(content_file)
                    with open(os.path.join(temp_folder, f"original_{file_name}"), "w", 
                              encoding="utf-8") as original_file:
                        original_file.write(html_content)
                    
                    # Parse HTML
                    soup = BeautifulSoup(html_content, 'html.parser')
                    
                    # Process all text nodes that need translation
                    # Common elements with translatable text
                    text_elements = soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
                                                  'li', 'th', 'td', 'figcaption', 'blockquote'])
                    
                    for element in text_elements:
                        # Get text content
                        text = element.get_text().strip()
                        
                        # Skip if empty or should not be translated
                        if not text or not should_translate(text):
                            continue
                        
                        # Generate a unique identifier for this element
                        element_id = element.get('id', '')
                        if not element_id:
                            # Create an XPath-like identifier
                            element_path = []
                            parent = element
                            while parent and parent.name != 'html':
                                if parent.name:
                                    position = 1
                                    for sibling in parent.previous_siblings:
                                        if sibling.name == parent.name:
                                            position += 1
                                    element_path.insert(0, f"{parent.name}[{position}]")
                                parent = parent.parent
                            element_id = '/'.join(element_path)
                        
                        count += 1
                        content_data.append({
                            "count": count,
                            "file": content_file,
                            "element_id": element_id,
                            "tag": element.name,
                            "value": text,
                            "html": str(element)
                        })
                        
            except Exception as e:
                app_logger.error(f"Error processing content file {content_file}: {str(e)}")
                import traceback
                app_logger.error(traceback.format_exc())
    
    app_logger.info(f"Total elements extracted: {count}")
    
    # Save the extracted data to JSON
    json_path = os.path.join(temp_folder, "src.json")
    
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(content_data, json_file, ensure_ascii=False, indent=4)

    return json_path


def write_translated_content_to_epub(file_path, original_json_path, translated_json_path):
    """
    Write translated content back to a new EPUB file, maintaining the original structure
    """
    # Load original and translated JSON data
    with open(original_json_path, "r", encoding="utf-8") as original_file:
        original_data = json.load(original_file)
    
    with open(translated_json_path, "r", encoding="utf-8") as translated_file:
        translated_data = json.load(translated_file)
    
    # Convert translations to a dictionary {count: translated_value}
    translations = {str(item["count"]): item["translated"] for item in translated_data}
    
    # Group original data by file
    file_elements = {}
    for element in original_data:
        file_name = element["file"]
        if file_name not in file_elements:
            file_elements[file_name] = []
        file_elements[file_name].append(element)
    
    # Create output directory
    result_folder = "result"
    os.makedirs(result_folder, exist_ok=True)
    
    # Create a new EPUB file
    original_filename = os.path.splitext(os.path.basename(file_path))[0]
    result_path = os.path.join(result_folder, f"{original_filename}_translated.epub")
    
    # Copy the original EPUB and modify it
    with zipfile.ZipFile(file_path, 'r') as original_epub:
        with zipfile.ZipFile(result_path, 'w') as new_epub:
            # Copy all files from original EPUB to new EPUB
            for item in original_epub.infolist():
                content = original_epub.read(item.filename)
                
                # If this is a content file that we've modified, update it
                if item.filename in file_elements:
                    # Get original content
                    html_content = content.decode('utf-8', errors='replace')
                    soup = BeautifulSoup(html_content, 'html.parser')
                    
                    # Track elements we've already replaced to avoid duplicates
                    replaced_elements = set()
                    
                    # Replace each element with its translation
                    for element_info in file_elements[item.filename]:
                        count = str(element_info["count"])
                        element_id = element_info["element_id"]
                        tag = element_info["tag"]
                        original_html = element_info["html"]
                        
                        # Skip if already replaced or no translation
                        if element_id in replaced_elements or count not in translations:
                            continue
                        
                        # Get the translated text
                        translated_text = translations[count]
                        
                        # Find the element to replace
                        if element_info.get("element_id", ""):
                            # Try to find by ID
                            target_element = soup.select_one(f"#{element_id}")
                        else:
                            # Try to find by matching the HTML content
                            # This is less reliable but a fallback
                            # Use BeautifulSoup to parse the original HTML
                            original_soup = BeautifulSoup(original_html, 'html.parser')
                            original_text = original_soup.get_text().strip()
                            
                            # Find all elements with the same tag
                            candidates = soup.find_all(tag)
                            target_element = None
                            
                            for candidate in candidates:
                                if candidate.get_text().strip() == original_text:
                                    target_element = candidate
                                    break
                        
                        if target_element:
                            # Create a new element with same attributes but new text
                            new_element = BeautifulSoup(f"<{tag}>{translated_text}</{tag}>", 'html.parser').find(tag)
                            
                            # Copy attributes
                            for attr, value in target_element.attrs.items():
                                new_element[attr] = value
                            
                            # Replace the element
                            target_element.replace_with(new_element)
                            replaced_elements.add(element_id)
                    
                    # Convert the modified soup back to HTML
                    modified_content = str(soup).encode('utf-8')
                    new_epub.writestr(item, modified_content)
                else:
                    # Just copy the file as is
                    new_epub.writestr(item, content)
    
    app_logger.info(f"Translated EPUB saved to: {result_path}")
    return result_path