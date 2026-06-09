import os
import requests
from dotenv import load_dotenv

load_dotenv()


class LLMAPI(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.headers.update({
            "Content-Type": "application/json"
        })
        self.base_url = os.getenv("LLM_API_URL")
        if not self.base_url:
            raise RuntimeError("LLM_API_URL not set in .env")

    def call(self, text: str):
        payload = {"text": text}
        response = self.post(self.base_url, json=payload)
        response.raise_for_status()
        return response.json()["response"]


if __name__ == "__main__":
    llm_api = LLMAPI()
    response = llm_api.call(
        "Generate a random integer between 1 and 100. "
        "Return only the number with no explanation"
    )
    print(response)
