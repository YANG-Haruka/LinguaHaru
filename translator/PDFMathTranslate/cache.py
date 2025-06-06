import os
import json
from peewee import Model, SqliteDatabase, AutoField, CharField, TextField, SQL
from typing import Optional
import glob
import uuid

# we don't init the database here
db = SqliteDatabase(None)

def display_database():
    data = [
        {
            "id": record.id,
            "translate_engine": record.translate_engine,
            "translate_engine_params": record.translate_engine_params,
            "original_text": record.original_text,
            "translation": record.translation,
        }
        for record in _TranslationCache.select().order_by(_TranslationCache.id)
    ]
    for entry in data:
        print(entry)

class _TranslationCache(Model):
    id = AutoField()
    translate_engine = CharField(max_length=20)
    translate_engine_params = TextField()
    original_text = TextField()
    translation = TextField()

    class Meta:
        database = db
        constraints = [
            SQL(
                """
            UNIQUE (
                translate_engine,
                translate_engine_params,
                original_text
                )
            ON CONFLICT REPLACE
            """
            )
        ]


class TranslationCache:
    @staticmethod
    def _sort_dict_recursively(obj):
        if isinstance(obj, dict):
            return {
                k: TranslationCache._sort_dict_recursively(v)
                for k in sorted(obj.keys())
                for v in [obj[k]]
            }
        elif isinstance(obj, list):
            return [TranslationCache._sort_dict_recursively(item) for item in obj]
        return obj

    def __init__(self, translate_engine: str, translate_engine_params: dict = None):
        assert (
            len(translate_engine) < 20
        ), "current cache require translate engine name less than 20 characters"
        self.translate_engine = translate_engine
        self.replace_params(translate_engine_params)

    # The program typically starts multi-threaded translation
    # only after cache parameters are fully configured,
    # so thread safety doesn't need to be considered here.
    def replace_params(self, params: dict = None):
        if params is None:
            params = {}
        self.params = params
        params = self._sort_dict_recursively(params)
        self.translate_engine_params = json.dumps(params)

    def update_params(self, params: dict = None):
        if params is None:
            params = {}
        self.params.update(params)
        self.replace_params(self.params)

    def add_params(self, k: str, v):
        self.params[k] = v
        self.replace_params(self.params)

    # Since peewee and the underlying sqlite are thread-safe,
    # get and set operations don't need locks.
    def get(self, original_text: str) -> Optional[str]:
        result = _TranslationCache.get_or_none(
            translate_engine=self.translate_engine,
            translate_engine_params=self.translate_engine_params,
            original_text=original_text,
        )
        return result.translation if result else None

    def set(self, original_text: str, translation: str):
        _TranslationCache.create(
            translate_engine=self.translate_engine,
            translate_engine_params=self.translate_engine_params,
            original_text=original_text,
            translation=translation,
        )

    # New method to extract all ids and original_text and save to JSON
    def export_translation_to_json(self,output_path):
        data = [
            {"count_src": record.id, "value": record.original_text}
            for record in _TranslationCache.select(_TranslationCache.id, _TranslationCache.original_text).order_by(_TranslationCache.id)
        ]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    # New method to update translations from a JSON file
    def update_translations_from_json(self,input_path):
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            count = int(item["count_src"])
            translated = item["translated"]
            _TranslationCache.update(translation=translated).where(_TranslationCache.id == count).execute()
        # display_database()
    
def generate_db_name():
    """Generate a unique database name with random UUID"""
    random_id = str(uuid.uuid4())[:8]  # Use first 8 characters of UUID
    return f"cache.v1.{random_id}.db"

def init_db(remove_exists=False):
    cache_folder = os.path.join(os.path.expanduser("~"), ".cache", "pdf2zh")
    os.makedirs(cache_folder, exist_ok=True)

    # Generate new database name with random string
    cache_db_path = os.path.join(cache_folder, generate_db_name())

    # If remove_exists is True, remove all existing database files
    if remove_exists:
        clean_all_dbs(cache_folder)

    # Initialize new database
    db.init(
        cache_db_path,
        pragmas={
            "journal_mode": "wal",  # Enable write-ahead logging
            "busy_timeout": 1000,   # Wait if database is busy
        },
    )
    db.create_tables([_TranslationCache], safe=True)
    return cache_db_path,cache_folder


def clean_all_dbs(cache_folder):
    """Clean all database files in the cache folder"""
    # Close any existing connections
    close_existing_db_connection()
    
    # Find and remove all database files and their associated WAL/SHM files
    db_pattern = os.path.join(cache_folder, "cache.v1.*.db")
    for db_file in glob.glob(db_pattern):
        try:
            # Remove main database file
            if os.path.exists(db_file):
                os.remove(db_file)
                # print(f"Removed database file: {db_file}")
            
            # Remove WAL file
            wal_file = db_file + "-wal"
            if os.path.exists(wal_file):
                os.remove(wal_file)
                # print(f"Removed WAL file: {wal_file}")
            
            # Remove SHM file
            shm_file = db_file + "-shm"
            if os.path.exists(shm_file):
                os.remove(shm_file)
                # print(f"Removed SHM file: {shm_file}")
                
        except PermissionError as e:
            print(f"PermissionError while removing {db_file}: {e}")
        except Exception as e:
            print(f"Error while removing {db_file}: {e}")
            
def close_existing_db_connection():
    """
    Close any active database connections to avoid file locking issues.
    """
    try:
        if not db.is_closed():
            db.close()  # Close the database connection if it’s open
            # print("Database connection closed.")
    except Exception as e:
        print(f"Error while closing the database connection: {e}")

def init_test_db():
    import tempfile

    cache_db_path = tempfile.mktemp(suffix=".db")
    test_db = SqliteDatabase(
        cache_db_path,
        pragmas={
            "journal_mode": "wal",
            "busy_timeout": 1000,
        },
    )
    test_db.bind([_TranslationCache], bind_refs=False, bind_backrefs=False)
    test_db.connect()
    test_db.create_tables([_TranslationCache], safe=True)
    return test_db


def clean_test_db(test_db):
    test_db.drop_tables([_TranslationCache])
    test_db.close()
    db_path = test_db.database
    if os.path.exists(db_path):
        os.remove(test_db.database)
    wal_path = db_path + "-wal"
    if os.path.exists(wal_path):
        os.remove(wal_path)
    shm_path = db_path + "-shm"
    if os.path.exists(shm_path):
        os.remove(shm_path)


def clean_db():
    if not db.is_closed():
        db.close()
    db_path = db.database
    if os.path.exists(db_path):
        os.remove(db_path)
    wal_path = db_path + "-wal"
    if os.path.exists(wal_path):
        os.remove(wal_path)
    shm_path = db_path + "-shm"
    if os.path.exists(shm_path):
        os.remove(shm_path)