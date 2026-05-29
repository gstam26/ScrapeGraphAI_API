import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SGAI_API_KEY")
FETCH_WAIT_MS = 3000
