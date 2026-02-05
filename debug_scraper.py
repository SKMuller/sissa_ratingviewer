import asyncio
from playwright.async_api import async_playwright

CLUB_URL = "https://ratingviewer.nl/lists/latest/clubs/020027"

async def debug_scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(CLUB_URL)
        await page.wait_for_selector(".rdt_TableRow")
        
        rows = await page.query_selector_all(".rdt_TableRow")
        print(f"Found {len(rows)} rows.")
        
        if rows:
            print("--- First Row Data ---")
            cells = await rows[0].query_selector_all("div[data-column-id]")
            for cell in cells:
                col_id = await cell.get_attribute("data-column-id")
                text = await cell.inner_text()
                print(f"Column ID: '{col_id}' | Text: '{text}'")
                
                # Check for link
                link = await cell.query_selector("a")
                if link:
                    href = await link.get_attribute("href")
                    print(f"  -> Link found: {href}")

        # Check pagination
        # Usually a select dropdown at the bottom
        dropdowns = await page.query_selector_all("select")
        print(f"--- Pagination ---")
        print(f"Found {len(dropdowns)} select dropdowns.")
        for i, dd in enumerate(dropdowns):
            val = await dd.input_value()
            print(f"Dropdown {i} value: {val}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_scrape())
