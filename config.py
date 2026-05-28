import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SGAI_API_KEY")
FETCH_WAIT_MS = 3000

PROMPT = """
Extract only factual sustainability claims from this page.
Exclude general marketing statements.
Return as a simple list called 'claims'.
"""

BRANDS = {
    "Ripple": "https://www.ripplefoods.com/our-story/",
    "Chobani": "https://www.chobani.com/impact/our-causes?tabValue=sustainability",
    "Silk": "https://silk.com/about-us/sustainability/",
    "Califia Farms": "https://www.califiafarms.com/sustainability/",
}