import asyncio
import json
import re
import os
import io
import zipfile
import requests
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright
import datetime

CLUB_URL = "https://ratingviewer.nl/lists/latest/clubs/020027"
OUTPUT_FILE = "sissa_ratings.json"
FIDE_XML_URL = "https://ratings.fide.com/download/standard_rating_list_xml.zip"

def get_previous_period_url():
    """Calculates the URL for the previous month's FIDE rating list."""
    today = datetime.date.today()
    # Go back to first day of this month, then minus one day to get prev month
    first_of_month = today.replace(day=1)
    prev_month_date = first_of_month - datetime.timedelta(days=1)
    
    # Format: standard_jan26frl_xml.zip
    mon = prev_month_date.strftime("%b").lower() # 'jan'
    yy = prev_month_date.strftime("%y") # '26'
    
    url = f"https://ratings.fide.com/download/standard_{mon}{yy}frl_xml.zip"
    return url

async def download_and_parse_fide_xml_async(url, page=None):
    """
    Downloads and parses a FIDE XML list from a given URL.
    First tries requests (HTTPS then HTTP with a browser User-Agent).
    Falls back to Playwright Chromium download if requests fail.
    Returns: {fide_id: {rating, games, title, country, birthday}}
    """
    print(f"Downloading FIDE List from {url}...")
    content = None
    temp_zip_path = None
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # 1. Try requests with HTTPS
    try:
        print("  -> Attempting download via requests (HTTPS)...")
        response = requests.get(url, headers=headers, timeout=(10, 30))
        if response.status_code == 200:
            content = response.content
            print("  -> Download successful.")
        else:
            print(f"  -> HTTPS failed with status code: {response.status_code}")
    except Exception as e:
        print(f"  -> requests (HTTPS) failed: {e}")
        
    # 2. Try requests with HTTP (port 80) if HTTPS failed
    if content is None:
        http_url = url.replace("https://", "http://")
        try:
            print("  -> Attempting download via requests (HTTP)...")
            response = requests.get(http_url, headers=headers, timeout=(10, 30))
            if response.status_code == 200:
                content = response.content
                print("  -> Download successful.")
            else:
                print(f"  -> HTTP failed with status code: {response.status_code}")
        except Exception as e:
            print(f"  -> requests (HTTP) failed: {e}")

    # 3. Try Playwright download if requests failed
    if content is None:
        try:
            print("  -> Attempting download via Playwright Chromium...")
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_zip_path = os.path.join(temp_dir, f"fide_{os.path.basename(url)}")
            if os.path.exists(temp_zip_path):
                try:
                    os.remove(temp_zip_path)
                except Exception:
                    pass
            
            if page is None:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context()
                    pg = await context.new_page()
                    async with pg.expect_download(timeout=60000) as download_info:
                        try:
                            await pg.goto(url, timeout=60000)
                        except Exception:
                            pass
                    download = await download_info.value
                    await download.save_as(temp_zip_path)
                    await browser.close()
            else:
                async with page.expect_download(timeout=60000) as download_info:
                    try:
                        await page.goto(url, timeout=60000)
                    except Exception:
                        pass
                download = await download_info.value
                await download.save_as(temp_zip_path)
                
            print("  -> Playwright download successful.")
        except Exception as e:
            print(f"  -> Playwright download failed: {e}")

    # 4. Parse ZIP file
    fide_data = {}
    try:
        zip_file_obj = None
        if content is not None:
            zip_file_obj = zipfile.ZipFile(io.BytesIO(content))
        elif temp_zip_path is not None and os.path.exists(temp_zip_path):
            zip_file_obj = zipfile.ZipFile(temp_zip_path)
            
        if zip_file_obj is not None:
            with zip_file_obj as z:
                xml_filename = [name for name in z.namelist() if name.endswith('.xml')][0]
                print(f"  -> Parsing {xml_filename}...")
                
                with z.open(xml_filename) as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    
                    for player in root.findall('player'):
                        fide_id = player.find('fideid').text
                        rating = player.find('rating').text
                        games = player.find('games').text
                        title = player.find('title').text
                        country = player.find('country').text
                        birthday = player.find('birthday').text
                        
                        if fide_id:
                            fide_data[fide_id] = {
                                "rating": int(rating) if rating and rating.isdigit() else 0,
                                "games": int(games) if games and games.isdigit() else 0,
                                "title": title if title else "",
                                "country": country if country else "",
                                "birthday": birthday if birthday else ""
                            }
            print(f"  -> Loaded {len(fide_data)} records.")
        else:
            print("  -> No download source available (both requests and Playwright failed).")
    except Exception as e:
        print(f"Error processing FIDE XML {url}: {e}")
    finally:
        if temp_zip_path is not None and os.path.exists(temp_zip_path):
            try:
                os.remove(temp_zip_path)
            except Exception:
                pass
                
    return fide_data

async def scrape_ratings(fide_only=False):
    # 1. Prepare FIDE Data (Current & Previous)
    if fide_only:
        print("--- FIDE Data Preparation ---")
        current_fide = await download_and_parse_fide_xml_async(FIDE_XML_URL)
        prev_url = get_previous_period_url()
        prev_fide = await download_and_parse_fide_xml_async(prev_url)
        print("-----------------------------")

        print("--- Running FIDE-Only Update ---")
        if not os.path.exists(OUTPUT_FILE):
            print(f"Error: {OUTPUT_FILE} does not exist. Run full scraper first to populate KNSB data.")
            return

        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        players = data.get("players", [])
        print(f"Loaded {len(players)} players from {OUTPUT_FILE}.")

        updated_count = 0
        for player in players:
            fide_id = player.get("fide_id")
            if fide_id:
                if fide_id in current_fide:
                    curr_rec = current_fide[fide_id]
                    player["fide_rating"] = str(curr_rec["rating"])
                    player["fide_games"] = str(curr_rec["games"])
                    
                    # Calculate Change
                    prev_rating = 0
                    if fide_id in prev_fide:
                        prev_rating = prev_fide[fide_id]["rating"]
                        if prev_rating > 0:
                            change = curr_rec["rating"] - prev_rating
                            player["fide_change"] = f"+{change}" if change > 0 else str(change)
                    else:
                        player["fide_change"] = "0"
                        
                    # Additional Details
                    if not player.get("title") and curr_rec["title"]:
                        player["title"] = curr_rec["title"]
                    if not player.get("country") and curr_rec["country"]:
                        player["country"] = curr_rec["country"]
                    if not player.get("birthday") and curr_rec["birthday"]:
                        player["birthday"] = curr_rec["birthday"]
                    
                    updated_count += 1
                    print(f"  -> Updated {player['name']} (FIDE ID {fide_id}): Rating {player['fide_rating']}, Change {player['fide_change']}")

        # Save to JSON
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "club": data.get("club", "JSV SISSA"),
                "timestamp": datetime.datetime.now().isoformat(),
                "players": players
            }, f, indent=4)
        
        print(f"Done! Updated {updated_count} players. Saved data to {OUTPUT_FILE}")
        return

    # Full scraping mode
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("--- FIDE Data Preparation ---")
        current_fide = await download_and_parse_fide_xml_async(FIDE_XML_URL, page=page)
        prev_url = get_previous_period_url()
        prev_fide = await download_and_parse_fide_xml_async(prev_url, page=page)
        print("-----------------------------")

        print(f"Navigating to {CLUB_URL}...")
        await page.goto(CLUB_URL)
        await page.wait_for_selector(".rdt_TableRow")

        players = []
        
        # Force pagination to 200
        try:
            print("Forcing pagination to 200 players...")
            await page.evaluate("""
                () => {
                    const select = document.querySelector('select[aria-label="Rows per page:"]');
                    if (select) {
                        const option = document.createElement('option');
                        option.value = '200';
                        option.text = '200';
                        select.appendChild(option);
                        select.value = '200';
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            """)
            await page.wait_for_timeout(3000) 
        except Exception as e:
            print(f"Pagination handling failed: {e}")

        # Grab all rows
        rows = await page.query_selector_all(".rdt_TableRow")
        print(f"Found {len(rows)} players in the list.")

        for row in rows:
            name_el = await row.query_selector("div[data-column-id='Name'] a")
            rating_el = await row.query_selector("div[data-column-id='Rating']")
            
            # New Columns
            title_el = await row.query_selector("div[data-column-id='3']")
            fed_el = await row.query_selector("div[data-column-id='4']")
            yob_el = await row.query_selector("div[data-column-id='6']")
            sex_el = await row.query_selector("div[data-column-id='7']")

            if name_el and rating_el:
                name = await name_el.inner_text()
                profile_url = await name_el.get_attribute("href")
                current_rating = (await rating_el.inner_text()).strip()
                
                # Extract texts
                title_val = (await title_el.inner_text()).strip() if title_el else ""
                fed_val = (await fed_el.inner_text()).strip() if fed_el else ""
                yob_val = (await yob_el.inner_text()).strip() if yob_el else ""
                sex_val = (await sex_el.inner_text()).strip() if sex_el else ""

                players.append({
                    "name": name,
                    "profile_url": f"https://ratingviewer.nl{profile_url}",
                    "current_rating": current_rating,
                    "rating_change": "0",
                    "games_played": "0",
                    "title": title_val,
                    "country": fed_val,
                    "birthday": yob_val,
                    "gender": sex_val,
                    # Internal field for processing
                    "_full_profile_url": f"https://ratingviewer.nl{profile_url}"
                })

        print(f"Basic data extracted for {len(players)} players. Fetching details...")

        # Fetch details for each player (KNSB Only)
        for i, player in enumerate(players):
            print(f"[{i+1}/{len(players)}] Processing {player['name']}...")
            try:
                await page.goto(player['_full_profile_url'])

                # 1. Get KNSB History (Latest Month) - Optimized using Summary Table
                try:
                    # Wait for ANY table to ensure page load
                    await page.wait_for_selector("table", timeout=5000)
                    
                    # Extract Games Played from Summary Table (Cell containing "#Gespeeld")
                    # Format: "#Gespeeld\n5"
                    games_cell = await page.query_selector("td:has-text('#Gespeeld')")
                    if games_cell:
                        text = await games_cell.inner_text()
                        # Take the number after the newline
                        if "\n" in text:
                            player["games_played"] = text.split("\n")[-1].strip()
                    
                    # Extract Rating Change from Calculation Cell
                    # Format: "Berekening\n\n2702=2692 + 10"
                    calc_cell = await page.query_selector("td:has-text('Berekening')")
                    if calc_cell:
                        text = await calc_cell.inner_text()
                        # Look for the last numbers " + 10" or " - 5" at the end of the string
                        # Simple parse: split by space, look for + or -
                        # Or regex search for [+-]\s?\d+$
                        import re
                        change_match = re.search(r'([+-])\s?(\d+)$', text.strip())
                        if change_match:
                            sign = change_match.group(1)
                            val = change_match.group(2)
                            # Re-construct: +10 or -5
                            if sign == "+":
                                player["rating_change"] = val # + is implied or we can add it later if needed, but JSON usually stores raw num key? 
                                # Current JSON has "15" (positive) or "-20"
                                # If sign is +, store as "10". If -, store as "-5"
                                player["rating_change"] = val
                            else:
                                player["rating_change"] = f"-{val}"
                        elif "=" in text:
                             # Fallback logic if regex fails but we have "A=B +/- C"
                             pass

                except Exception as e:
                    print(f"  -> Error KNSB stats: {e}")

                # 2. Get FIDE ID (from Profile Link)
                fide_link = await page.query_selector("a[href*='ratings.fide.com/profile/']")
                if fide_link:
                    fide_url = await fide_link.get_attribute("href")
                    # Extract ID from URL
                    match = re.search(r'profile/(\d+)', fide_url)
                    if match:
                        fide_id = match.group(1)
                        player["fide_id"] = fide_id
                        
                        # 3. Match with FIDE Data
                        if fide_id in current_fide:
                            curr_rec = current_fide[fide_id]
                            player["fide_rating"] = str(curr_rec["rating"])
                            player["fide_games"] = str(curr_rec["games"])
                            
                            # Calculate Change
                            prev_rating = 0
                            if fide_id in prev_fide:
                                prev_rating = prev_fide[fide_id]["rating"]
                                if prev_rating > 0:
                                     change = curr_rec["rating"] - prev_rating
                                     # Format change: "+15", "-5", "0"
                                     player["fide_change"] = f"+{change}" if change > 0 else str(change)
                            else:
                                player["fide_change"] = "0" # No history found
                                
                            # Additional Details: List data is primary.
                            # If list data was empty/missing, maybe FIDE has it?
                            if not player["title"] and curr_rec["title"]:
                                player["title"] = curr_rec["title"]
                            if not player["country"] and curr_rec["country"]:
                                player["country"] = curr_rec["country"]
                            if not player["birthday"] and curr_rec["birthday"]:
                                player["birthday"] = curr_rec["birthday"]
                            
                            print(f"  -> FIDE {fide_id}: Rating {curr_rec['rating']}, Change {player['fide_change']}, Games {curr_rec['games']}")
                            print(f"  -> Details: {player.get('title','')} | {player.get('country','')} | {player.get('birthday','')}")
                        else:
                            print(f"  -> FIDE ID {fide_id} found but not in XML list (inactive/unrated?)")

            except Exception as e:
                print(f"Error processing {player['name']}: {e}")

        # Cleanup internal fields
        for p in players:
            if "_full_profile_url" in p: del p["_full_profile_url"]

        # Save to JSON
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "club": "JSV SISSA",
                "timestamp": datetime.datetime.now().isoformat(),
                "players": players
            }, f, indent=4)
        
        print(f"Done! Saved data to {OUTPUT_FILE}")
        await browser.close()

if __name__ == "__main__":
    import sys
    fide_only = "--fide-only" in sys.argv or "-f" in sys.argv
    asyncio.run(scrape_ratings(fide_only=fide_only))
