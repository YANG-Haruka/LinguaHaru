import os
import shutil

def create_custom_language_prompt_file(custom_language_name):
    """Create a new prompt file for a custom language by copying en.json"""
    prompts_dir = os.path.join("config", "prompts")
    source_file = os.path.join(prompts_dir, "en.json")
    target_file = os.path.join(prompts_dir, f"{custom_language_name}.json")
    
    try:
        # Ensure the prompts directory exists
        os.makedirs(prompts_dir, exist_ok=True)
        
        # Copy en.json to the new custom language file
        if os.path.exists(source_file):
            shutil.copy2(source_file, target_file)
            return True, f"Created prompt file for {custom_language_name}"
        else:
            return False, "Source file en.json not found"
    except Exception as e:
        return False, f"Error creating prompt file: {str(e)}"

def add_custom_language(custom_language_name):
    """Add a custom language to the system"""
    if not custom_language_name or custom_language_name.strip() == "":
        return False, "Language name cannot be empty"
    
    custom_language_name = custom_language_name.strip()
    
    # Create the prompt file
    success, message = create_custom_language_prompt_file(custom_language_name)
    
    if success:
        return True, f"Custom language '{custom_language_name}' added successfully"
    else:
        return False, message


def get_available_languages():
    """Read language files from config/prompts directory and return display names"""
    prompts_dir = os.path.join("config", "prompts")
    available_languages = []
    
    if os.path.exists(prompts_dir):
        # Get all .json files in the prompts directory
        for filename in os.listdir(prompts_dir):
            if filename.endswith(".json"):
                # Get language code without extension
                lang_code = os.path.splitext(filename)[0]
                
                # Find the display name from LANGUAGE_MAP
                display_name_found = False
                for display_name, code in LANGUAGE_MAP.items():
                    if code == lang_code:
                        available_languages.append(display_name)
                        display_name_found = True
                        break
                
                # If language code not found in LANGUAGE_MAP, add it directly
                if not display_name_found:
                    available_languages.append(lang_code)  # Changed from lang_code.upper()
    
    # If no languages found, return default list
    if not available_languages:
        available_languages = [
            "English", "中文", "繁體中文", "日本語", "Español", 
            "Français", "Deutsch", "Italiano", "Português", 
            "Русский", "한국어", "ภาษาไทย", "Tiếng Việt"
        ]
    
    return sorted(set(available_languages))

def get_language_code(display_name):
    """Get language code from display name, supporting custom languages"""
    # First check if it's in the existing LANGUAGE_MAP
    if display_name in LANGUAGE_MAP:
        return LANGUAGE_MAP[display_name]
    
    # If not found, assume the display name is the language code in uppercase
    # Convert it to lowercase to match file naming convention
    return display_name.lower()

LANGUAGE_MAP = {
    "日本語": "ja",
    "中文": "zh",
    "繁體中文": "zh-Hant",
    "English": "en",
    "Español": "es",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
    "Português": "pt",
    "Русский": "ru",
    "한국어": "ko",
    "ภาษาไทย": "th",
    "Tiếng Việt": "vi"
}

LABEL_TRANSLATIONS = {
    # English
    "en": {
        "Source Language": "Source Language",
        "Target Language": "Target Language",
        "Use Online Model": "Use Online Model",
        "Models": "Models",
        "API Key": "API Key",
        "Upload File": "Upload Office File (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Upload Files (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Download Translated File",
        "Status Message": "Status Message",
        "Translate": "Translate",
        "Local Network Mode (Restart to Apply)": "Local Network Mode (Restart to Apply)",
        "Max Retries": "Max Retries",
        "Excel Mode": "Missing Translation? Try Mode 2",
        "Word Bilingual": "Enable Bilingual Comparison",
        "Continue Translation": "Continue Translation",
        "Thread Count": "Thread Count",
        "Stop Translation": "Stop Translation",
        "Stopping": "Stopping..."
    },
    # Simplified Chinese
    "zh": {
        "Source Language": "源语言",
        "Target Language": "目标语言",
        "Use Online Model": "使用在线模型",
        "Models": "模型",
        "API Key": "API 密钥",
        "Upload File": "上传文件 (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "上传文件 (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "下载翻译文件",
        "Status Message": "状态消息",
        "Translate": "翻译",
        "Local Network Mode (Restart to Apply)": "局域网模式（重启后生效）",
        "Max Retries": "最大重试次数",
        "Excel Mode": "有漏翻？尝试模式2",
        "Word Bilingual": "启用双语对照",
        "Continue Translation": "继续翻译",
        "Thread Count": "线程数量",
        "Stop Translation": "停止翻译",
        "Stopping": "正在停止..."
    },
    # Traditional Chinese
    "zh-Hant": {
        "Source Language": "來源語言",
        "Target Language": "目標語言",
        "Use Online Model": "使用線上模型",
        "Models": "模型",
        "API Key": "API 金鑰",
        "Upload File": "上傳檔案 (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "上傳檔案 (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "下載翻譯後的檔案",
        "Status Message": "狀態訊息",
        "Translate": "翻譯",
        "Local Network Mode (Restart to Apply)": "區域網路模式（重新啟動後生效）",
        "Max Retries": "最大重試次數",
        "Excel Mode": "有漏翻？嘗試模式2",
        "Word Bilingual": "啟用雙語對照",
        "Continue Translation": "繼續翻譯",
        "Thread Count": "執行緒數量",
        "Stop Translation": "停止翻譯",
        "Stopping": "正在停止..."
    },
    # Japanese
    "ja": {
        "Source Language": "ソース言語",
        "Target Language": "ターゲット言語",
        "Use Online Model": "オンラインモデルを使用",
        "Models": "モデル",
        "API Key": "APIキー",
        "Upload File": "ファイルをアップロード (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "ファイルをアップロード (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "翻訳ファイルをダウンロード",
        "Status Message": "ステータスメッセージ",
        "Translate": "翻訳",
        "Local Network Mode (Restart to Apply)": "ローカルネットワークモード（再起動後に適用）",
        "Max Retries": "最大再試行回数",
        "Excel Mode": "翻訳漏れ？モード2を試す",
        "Word Bilingual": "二言語対照を有効にする",
        "Continue Translation": "翻訳を続ける",
        "Thread Count": "スレッド数",
        "Stop Translation": "翻訳を停止",
        "Stopping": "停止中..."
    },
    # Spanish
    "es": {
        "Source Language": "Idioma de origen",
        "Target Language": "Idioma de destino",
        "Use Online Model": "Usar modelo en línea",
        "Models": "Modelos",
        "API Key": "Clave API",
        "Upload File": "Subir archivo (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Subir archivos (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Descargar archivo traducido",
        "Status Message": "Mensaje de estado",
        "Translate": "Traducir",
        "Local Network Mode (Restart to Apply)": "Modo de red local (Reiniciar para aplicar)",
        "Max Retries": "Número máximo de reintentos",
        "Excel Mode": "¿Traducción incompleta? Probar modo 2",
        "Word Bilingual": "Habilitar comparación bilingüe",
        "Continue Translation": "Continuar traducción",
        "Thread Count": "Número de hilos",
        "Stop Translation": "Detener traducción",
        "Stopping": "Deteniendo..."
    },
    # French
    "fr": {
        "Source Language": "Langue source",
        "Target Language": "Langue cible",
        "Use Online Model": "Utiliser un modèle en ligne",
        "Models": "Modèles",
        "API Key": "Clé API",
        "Upload File": "Télécharger le fichier (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Télécharger les fichiers (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Télécharger le fichier traduit",
        "Status Message": "Message d'état",
        "Translate": "Traduire",
        "Local Network Mode (Restart to Apply)": "Mode réseau local (Redémarrer pour appliquer)",
        "Max Retries": "Nombre maximal de tentatives",
        "Excel Mode": "Traduction incomplète ? Essayez le mode 2",
        "Word Bilingual": "Activer la comparaison bilingue",
        "Continue Translation": "Continuer la traduction",
        "Thread Count": "Nombre de threads",
        "Stop Translation": "Arrêter la traduction",
        "Stopping": "Arrêt en cours..."
    },
    # German
    "de": {
        "Source Language": "Ausgangssprache",
        "Target Language": "Zielsprache",
        "Use Online Model": "Online-Modell verwenden",
        "Models": "Modelle",
        "API Key": "API-Schlüssel",
        "Upload File": "Datei hochladen (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Dateien hochladen (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Übersetzte Datei herunterladen",
        "Status Message": "Statusnachricht",
        "Translate": "Übersetzen",
        "Local Network Mode (Restart to Apply)": "Lokaler Netzwerkmodus (Neustart erforderlich)",
        "Max Retries": "Maximale Wiederholungsversuche",
        "Excel Mode": "Übersetzung unvollständig? Versuchen Sie Modus 2",
        "Word Bilingual": "Zweisprachigen Vergleich aktivieren",
        "Continue Translation": "Übersetzung fortsetzen",
        "Thread Count": "Thread-Anzahl",
        "Stop Translation": "Übersetzung stoppen",
        "Stopping": "Stoppe..."
    },
    # Italian
    "it": {
        "Source Language": "Lingua di origine",
        "Target Language": "Lingua di destinazione",
        "Use Online Model": "Usa modello online",
        "Models": "Modelli",
        "API Key": "Chiave API",
        "Upload File": "Carica file (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Carica file (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Scarica file tradotto",
        "Status Message": "Messaggio di stato",
        "Translate": "Traduci",
        "Local Network Mode (Restart to Apply)": "Modalità rete locale (Riavvia per applicare)",
        "Max Retries": "Numero massimo di tentativi",
        "Excel Mode": "Traduzione incompleta? Prova la modalità 2",
        "Word Bilingual": "Abilita confronto bilingue",
        "Continue Translation": "Continua traduzione",
        "Thread Count": "Numero di thread",
        "Stop Translation": "Interrompi traduzione",
        "Stopping": "Interruzione in corso..."
    },
    # Portuguese
    "pt": {
        "Source Language": "Idioma de origem",
        "Target Language": "Idioma de destino",
        "Use Online Model": "Usar modelo online",
        "Models": "Modelos",
        "API Key": "Chave de API",
        "Upload File": "Enviar arquivo (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Enviar arquivos (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Baixar arquivo traduzido",
        "Status Message": "Mensagem de status",
        "Translate": "Traduzir",
        "Local Network Mode (Restart to Apply)": "Modo de rede local (Reiniciar para aplicar)",
        "Max Retries": "Número máximo de tentativas",
        "Excel Mode": "Tradução incompleta? Tente o modo 2",
        "Word Bilingual": "Ativar comparação bilíngue",
        "Continue Translation": "Continuar tradução",
        "Thread Count": "Número de threads",
        "Stop Translation": "Parar tradução",
        "Stopping": "Parando..."
    },
    # Russian
    "ru": {
        "Source Language": "Исходный язык",
        "Target Language": "Целевой язык",
        "Use Online Model": "Использовать онлайн-модель",
        "Models": "Модели",
        "API Key": "API-ключ",
        "Upload File": "Загрузить файл (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Загрузить файлы (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Скачать переведенный файл",
        "Status Message": "Статусное сообщение",
        "Translate": "Перевести",
        "Local Network Mode (Restart to Apply)": "Режим локальной сети (Перезагрузка для применения)",
        "Max Retries": "Максимальное количество повторных попыток",
        "Excel Mode": "Перевод неполный? Попробуйте режим 2",
        "Word Bilingual": "Включить двуязычное сравнение",
        "Continue Translation": "Продолжить перевод",
        "Thread Count": "Количество потоков",
        "Stop Translation": "Остановить перевод",
        "Stopping": "Остановка..."
    },
    # Korean
    "ko": {
        "Source Language": "소스 언어",
        "Target Language": "대상 언어",
        "Use Online Model": "온라인 모델 사용",
        "Models": "모델",
        "API Key": "API 키",
        "Upload File": "파일 업로드 (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "파일 업로드 (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "번역된 파일 다운로드",
        "Status Message": "상태 메시지",
        "Translate": "번역",
        "Local Network Mode (Restart to Apply)": "로컬 네트워크 모드 (적용하려면 재시작)",
        "Max Retries": "최대 재시도 횟수",
        "Excel Mode": "번역이 누락되었나요? 모드 2 시도",
        "Word Bilingual": "이중 언어 비교 활성화",
        "Continue Translation": "번역 계속하기",
        "Thread Count": "스레드 수",
        "Stop Translation": "번역 중지",
        "Stopping": "중지 중..."
    },
    # Thai
    "th": {
        "Source Language": "ภาษาต้นฉบับ",
        "Target Language": "ภาษาเป้าหมาย",
        "Use Online Model": "ใช้โมเดลออนไลน์",
        "Models": "โมเดล",
        "API Key": "คีย์ API",
        "Upload File": "อัปโหลดไฟล์ (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "อัปโหลดไฟล์ (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "ดาวน์โหลดไฟล์ที่แปลแล้ว",
        "Status Message": "ข้อความสถานะ",
        "Translate": "แปล",
        "Local Network Mode (Restart to Apply)": "โหมดเครือข่ายท้องถิ่น (รีสตาร์ทเพื่อใช้งาน)",
        "Max Retries": "จำนวนการลองซ้ำสูงสุด",
        "Excel Mode": "การแปลขาดหายไป? ลองโหมด 2",
        "Word Bilingual": "เปิดใช้งานการเปรียบเทียบสองภาษา",
        "Continue Translation": "แปลต่อ",
        "Thread Count": "จำนวนเธรด",
        "Stop Translation": "หยุดการแปล",
        "Stopping": "กำลังหยุด..."
    },
    # Vietnamese
    "vi": {
        "Source Language": "Ngôn ngữ nguồn",
        "Target Language": "Ngôn ngữ đích",
        "Use Online Model": "Sử dụng mô hình trực tuyến",
        "Models": "Mô hình",
        "API Key": "Khóa API",
        "Upload File": "Tải lên tệp (.docx, .pptx, .xlsx, .pdf)",
        "Upload Files": "Tải lên tệp (.docx, .pptx, .xlsx, .pdf, .srt)",
        "Download Translated File": "Tải xuống tệp đã dịch",
        "Status Message": "Thông báo trạng thái",
        "Translate": "Dịch",
        "Local Network Mode (Restart to Apply)": "Chế độ mạng cục bộ (Khởi động lại để áp dụng)",
        "Max Retries": "Số lần thử lại tối đa",
        "Excel Mode": "Bị bỏ sót khi dịch? Thử chế độ 2",
        "Word Bilingual": "Bật so sánh song ngữ",
        "Continue Translation": "Tiếp tục dịch",
        "Thread Count": "Số lượng luồng",
        "Stop Translation": "Dừng dịch",
        "Stopping": "Đang dừng..."
    }
}