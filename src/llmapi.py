import os
import time

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

    def call(
        self,
        text: str,
        timeout: int | float | None = None,
        max_attempts: int = 2,
        retry_wait_s: float = 5,
    ):
        """POST text to the Power Automate flow.

        Retries once on a 5xx response — the proxy intermittently returns
        502 Bad Gateway under load; without a retry that chunk's cells are
        silently blanked. Timeouts are NOT retried (caller handles them).
        """
        payload = {"text": text}
        response = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.post(self.base_url, json=payload, timeout=timeout)
            except requests.exceptions.Timeout as exc:
                raise TimeoutError(str(exc)) from exc
            if response.status_code >= 500 and attempt < max_attempts:
                print(
                    f"      ! LLMAPI {response.status_code} "
                    f"(attempt {attempt}/{max_attempts}); retrying in {retry_wait_s}s..."
                )
                time.sleep(retry_wait_s)
                continue
            break
        response.raise_for_status()
        return response.json()["response"]


if __name__ == "__main__":
    llm_api = LLMAPI()
    response = llm_api.call(
        "Generate a random integer between 1 and 100. "
        "Return only the number with no explanation"
    )
    print(response)
