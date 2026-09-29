import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


import urllib.parse

def _get_db_url():
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")
    
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST", "127.0.0.1")
    db_port = os.getenv("DB_PORT", "3306")
    db_name = os.getenv("DB_NAME", "garment_erp")
    
    if db_user:
        pwd_part = f":{urllib.parse.quote_plus(db_pass)}" if db_pass is not None else ""
        return f"mysql+pymysql://{db_user}{pwd_part}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    
    return "mysql+pymysql://erp:erp_pass@127.0.0.1:3306/garment_erp?charset=utf8mb4"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    DATABASE_URL = _get_db_url()
    TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "12"))
    JSON_SORT_KEYS = False

