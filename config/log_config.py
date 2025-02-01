import logging
import sys
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

def setup_logger(name="app_logger"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    if not logger.hasHandlers():
        console_handler = logging.StreamHandler(sys.stdout)
        # console_handler.setLevel(logging.DEBUG)
        console_handler.setLevel(logging.DEBUG)
        
        formatter = SimpleColoredFormatter(fmt='%(message)s')
        console_handler.setFormatter(formatter)
        
        logger.addHandler(console_handler)
    
    return logger

# 创建并导出 logger
app_logger = setup_logger()
