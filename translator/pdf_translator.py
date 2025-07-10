from textProcessing.base_translator import DocumentTranslator
from llmWrapper.offline_translation import _get_host, _detect_lm_studio_port
from llmWrapper.online_translation import load_model_config
import subprocess
import os
import sys
import shutil
from pathlib import Path
from babeldoc.format.pdf import high_level
from babeldoc.assets.assets import restore_offline_assets_package

class PdfTranslator(DocumentTranslator):
    def get_babeldoc_executable(self):
        """Get babeldoc executable path"""
        if getattr(sys, 'frozen', False):
            # Packaged program, find bundled babeldoc
            if sys.platform == "win32":
                babeldoc_name = "babeldoc.exe"
            else:
                babeldoc_name = "babeldoc"
            
            # Check _MEIPASS directory first
            bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            babeldoc_path = os.path.join(bundle_dir, babeldoc_name)
            
            if os.path.exists(babeldoc_path):
                return babeldoc_path
        
        # Development environment or system installed babeldoc
        babeldoc_path = shutil.which('babeldoc')
        if babeldoc_path:
            return babeldoc_path
        
        raise RuntimeError("babeldoc executable not found. Please ensure babeldoc is installed.")

    def get_resource_path(self, relative_path):
        """Get absolute path for resource files"""
        if getattr(sys, 'frozen', False):
            # Packaged program
            base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        else:
            # Development environment
            base_path = os.path.dirname(os.path.abspath(__file__))
        
        return os.path.join(base_path, relative_path)

    def run_babeldoc(self, progress_callback=None):
        if progress_callback:
            progress_callback(0, desc="Translation has started. Please check the progress in the terminal")

        high_level.init()
        
        # Use dynamic path for resource files
        assets_path = self.get_resource_path("models/offline_assets_113acc2997e19c97a2977ccf91fc029d1b2ce1ba807e39de6da811c434ee61bf.zip")
        restore_offline_assets_package(Path(assets_path))

        # Configure API settings
        if self.use_online:
            api_key = self.api_key
            model_name = self.model
            model_config = load_model_config()
            base_url = model_config.get("base_url")
            model_name = model_config.get("model")
        else:
            api_key = "ollama"
            if "(Ollama)" in self.model:
                model_name = self.model.split(")", 1)[1].strip()
                host, port = _get_host()
                if host.endswith('/'):
                    host = host.rstrip('/')
                if not host.startswith('http'):
                    host = f"http://{host}"
                base_url = f"{host}:{port}/v1"
            else:
                model_name = self.model.split(")", 1)[1].strip()
                host, port = _detect_lm_studio_port()
                if host.endswith('/'):
                    host = host.rstrip('/')
                if not host.startswith('http'):
                    host = f"http://{host}"
                base_url = f"{host}:{port}/v1"

        # Use dynamically obtained babeldoc path
        babeldoc_exe = self.get_babeldoc_executable()
        
        cmd = [
            babeldoc_exe, "--openai",
            "--watermark-output-mode","no_watermark",
            "--openai-base-url", base_url,
            "--openai-model", model_name,
            "--openai-api-key", api_key,
            "--files", self.input_file_path,
            "--translate-table-text",
            "--no-dual",
            "-o", "result",
        ]

        try:
            result = subprocess.run(cmd, check=True)
            if result.stderr:
                print(result.stderr)
                
        except subprocess.CalledProcessError as e:
            error_message = f"babeldoc command failed with return code {e.returncode}"
            if e.stdout:
                error_message += f"\n\nOutput:\n{e.stdout}"
            if e.stderr:
                error_message += f"\n\nError:\n{e.stderr}"
            raise RuntimeError(error_message)
        except FileNotFoundError:
            raise RuntimeError("babeldoc command not found. Please ensure babeldoc is installed and in PATH.")
        except Exception as e:
            raise RuntimeError(f"Unexpected error running babeldoc: {str(e)}")

    def process(self, file_name, file_extension, progress_callback=None):
        if file_extension == ".pdf":
            self.run_babeldoc(progress_callback)
            
            if progress_callback:
                progress_callback(100, desc="PDF translation completed!")
            
            base_name = os.path.splitext(os.path.basename(self.input_file_path))[0]
            original_filename = f"result/{base_name}.no_watermark.zh.mono.pdf"
            translated_filename = f"result/{base_name}_translated.pdf"
            
            # Rename the output file
            if os.path.exists(original_filename):
                # Remove existing translated file if it exists
                if os.path.exists(translated_filename):
                    os.remove(translated_filename)
                os.rename(original_filename, translated_filename)
            
            return translated_filename, {}
        
        return super().process(file_name, file_extension, progress_callback)