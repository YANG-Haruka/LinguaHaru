import logging
import sys
import os
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

class SimpleColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': Fore.BLUE,
        'INFO': Fore.GREEN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.RED
    }

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, Fore.WHITE)
        levelname = record.levelname
        msg = super().format(record)
        return f"{log_color}[{levelname}] {msg}{Style.RESET_ALL}"

class FileLogger:
    def __init__(self, name="app_logger", console_level=logging.INFO, file_level=logging.DEBUG):
        """
        Initialize the file logger with console and file handlers.
        
        Args:
            name: Logger name
            console_level: Logging level for console output
            file_level: Logging level for file output
        """
        self.name = name
        self.console_level = console_level
        self.file_level = file_level
        self.logger = logging.getLogger(name)
        self.logger.setLevel(min(console_level, file_level))
        self.file_handler = None
        
        # Set up console handler (only once)
        if not self.logger.hasHandlers():
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(console_level)
            console_formatter = SimpleColoredFormatter(fmt='%(message)s')
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)
    
    def create_file_log(self, filename):
        """
        Create a new log file for the specified filename.
        
        Args:
            filename: Name of the file being processed
            
        Returns:
            Path to the created log file
        """
        # Remove existing file handler if present
        if self.file_handler and self.file_handler in self.logger.handlers:
            self.logger.removeHandler(self.file_handler)
            self.file_handler.close()
        
        # Create log directory if it doesn't exist
        log_dir = "log"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Clean filename, remove unsafe characters
        safe_filename = os.path.basename(filename)
        # Remove characters that may cause invalid filenames
        safe_filename = ''.join(c for c in safe_filename if c.isalnum() or c in '._- ')
        
        # Generate timestamp for log filename
        current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file = os.path.join(log_dir, f"{current_time}_{safe_filename}.log")
        
        # Create new file handler
        self.file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        self.file_handler.setLevel(self.file_level)
        file_formatter = logging.Formatter(fmt='%(asctime)s - [%(levelname)s] - %(message)s', 
                                           datefmt='%Y-%m-%d %H:%M:%S')
        self.file_handler.setFormatter(file_formatter)
        self.logger.addHandler(self.file_handler)
        
        self.logger.info(f"Started processing file: {safe_filename}")
        return log_file
    
    def get_logger(self):
        """
        Get the configured logger instance.
        
        Returns:
            Logger instance
        """
        return self.logger

# Create file logger instance
file_logger = FileLogger(console_level=logging.INFO, file_level=logging.DEBUG)
app_logger = file_logger.get_logger()