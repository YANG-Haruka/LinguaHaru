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

def setup_logger(name="app_logger"):
    log_dir = "log"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(log_dir, f"{current_time}_app.log")

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.hasHandlers():
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_formatter = SimpleColoredFormatter(fmt='%(message)s')
        console_handler.setFormatter(console_formatter)

        # File handler
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(fmt='%(asctime)s - [%(levelname)s] - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler.setFormatter(file_formatter)

        # add logger
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger

app_logger = setup_logger()
