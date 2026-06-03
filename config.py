import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SGAI_API_KEY")

if not API_KEY:
    raise ValueError("Missing SGAI_API_KEY. Add it to your .env file.")

ACQUIRE_TOOL = "requests"
EXTRACT_TOOL = "sgai"

FETCH_WAIT_MS = 3000

CACHE_DIR = "cache"
OUTPUT_DIR = "outputs"

VERIFY_THRESHOLD = 70