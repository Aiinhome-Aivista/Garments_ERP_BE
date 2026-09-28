import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://erp:erp_pass@127.0.0.1:3306/garment_erp?charset=utf8mb4",
    )
    TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "12"))
    JSON_SORT_KEYS = False
