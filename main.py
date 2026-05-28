from scrapegraph_py import ScrapeGraphAI, JsonFormatConfig, FetchConfig
from config import API_KEY, FETCH_WAIT_MS, PROMPT, BRANDS

sgai = ScrapeGraphAI(api_key=API_KEY)

for brand, url in BRANDS.items():
    print(f"\n--- {brand} ---")

    result = sgai.scrape(
        url,
        formats=[JsonFormatConfig(prompt=PROMPT)],
        fetch_config=FetchConfig(mode="js", wait=FETCH_WAIT_MS)
    )

    claims = result.data.results["json"]["data"].get("claims", [])

    if claims:
        for i, claim in enumerate(claims, 1):
            print(f"{i}. {claim}")
    else:
        print("No claims returned")

sgai.close()