"""
Parivahan Fancy Number Fetcher
================================
Fetches all available/booked registration numbers from the Parivahan
fancy number website and exports them to a CSV with pattern categories.

Usage:
    python main.py

Features:
- Manual login (you handle username, password, OTP, captcha)
- Incremental CSV writing (saves after every page)
- Resume support (detects existing CSV and skips already-fetched pages)
- Pattern categorization (XXXX, XYYY, YYYX, XXYY, etc.)
"""

import csv
import os
import re
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager


# =============================================================================
# Configuration
# =============================================================================
FUEL_TYPE = "PETROL"
VEHICLE_CATEGORY = "LMV"  # Value for LIGHT MOTOR VEHICLE
VEHICLE_SERIES = "DL11CG" # DL5CY, DL7CY
LOGIN_URL = "https://fancy.parivahan.gov.in/"
HOME_URL = "https://fancy.parivahan.gov.in/fancy/faces/app/applicanthome.xhtml"

# Known background colors:
# Available: #e4f8e7 → rgba(228, 248, 231, 1)  — light green
# Booked:    #ff8900 → rgba(255, 137, 0, 1)    — orange

# Output file (fixed name so we can resume)
SCRIPT_DIR = Path(__file__).parent
OUTPUT_FILE = SCRIPT_DIR / f"{VEHICLE_SERIES}_numbers.csv"

# Delay between page navigations (seconds) — increase if the site is slow
PAGE_DELAY = 3
DROPDOWN_DELAY = 4
NUMBERS_PER_PAGE = 100


# =============================================================================
# Number Pattern Categorization
# =============================================================================
def categorize_number(num_str: str) -> str:
    """Categorize a 4-digit number string into a pattern category."""
    if len(num_str) != 4 or not num_str.isdigit():
        return "OTHER"

    a, b, c, d = num_str

    # XXXX - All same (e.g., 1111)
    if a == b == c == d:
        return "XXXX"

    # XYYY - First different, last 3 same (e.g., 1222)
    if b == c == d and a != b:
        return "XYYY"

    # YYYX - First 3 same, last different (e.g., 2221)
    if a == b == c and d != a:
        return "YYYX"

    # XXYY - First 2 same, last 2 same (e.g., 1122)
    if a == b and c == d and a != c:
        return "XXYY"

    # XYXY - Alternating pair (e.g., 1212)
    if a == c and b == d and a != b:
        return "XYXY"

    # XYYX - Palindrome (e.g., 1221)
    if a == d and b == c and a != b:
        return "XYYX"

    # XXYZ - First 2 same, last 2 different from each other and from first
    if a == b and c != d and c != a and d != a:
        return "XXYZ"

    # XYZZ - Last 2 same, first 2 different from each other and from last
    if c == d and a != b and a != c and b != c:
        return "XYZZ"

    # SEQUENTIAL - Ascending or descending consecutive digits
    digits = [int(ch) for ch in num_str]
    diffs = [digits[i + 1] - digits[i] for i in range(3)]
    if diffs == [1, 1, 1] or diffs == [-1, -1, -1]:
        return "SEQUENTIAL"

    return "OTHER"


# =============================================================================
# Determine availability from an element's background color
# =============================================================================
def get_availability(label_element) -> str:
    """
    Determine if a number is available or booked by checking its
    background color via two methods for reliability.
    
    Available: #e4f8e7 → rgba(228, 248, 231, 1)
    Booked:    #ff8900 → rgba(255, 137, 0, 1)
    """
    # Method 1: Computed CSS (Selenium normalizes to rgba)
    bg = (label_element.value_of_css_property("background-color") or "").lower().replace(" ", "")

    if "228,248,231" in bg:
        return "Yes"
    if "255,137,0" in bg:
        return "No"

    # Method 2: Raw style attribute (hex colors)
    style = (label_element.get_attribute("style") or "").lower()
    if "e4f8e7" in style:
        return "Yes"
    if "ff8900" in style:
        return "No"

    return "Unknown"


# =============================================================================
# PrimeFaces Dropdown Helper
# =============================================================================
def select_primefaces_dropdown(driver, wait, dropdown_id: str, value_text: str):
    """
    Select a value from a PrimeFaces SelectOneMenu dropdown.
    These are NOT standard <select> elements — they use custom UI.
    """
    label = wait.until(EC.element_to_be_clickable((By.ID, f"{dropdown_id}_label")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", label)
    time.sleep(0.5)
    label.click()
    time.sleep(1)

    panel_id = f"{dropdown_id}_panel"
    panel = wait.until(EC.visibility_of_element_located((By.ID, panel_id)))

    items = panel.find_elements(By.CSS_SELECTOR, "li.ui-selectonemenu-item")
    for item in items:
        if item.text.strip().upper() == value_text.upper():
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item)
            time.sleep(0.3)
            item.click()
            print(f"  ✓ Selected '{value_text}' in '{dropdown_id}'")
            return True

    # Fallback: partial match
    for item in items:
        if value_text.upper() in item.text.strip().upper():
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item)
            time.sleep(0.3)
            item.click()
            print(f"  ✓ Selected '{item.text.strip()}' (partial) in '{dropdown_id}'")
            return True

    print(f"  ✗ Could not find '{value_text}' in '{dropdown_id}'")
    return False


# =============================================================================
# Extract numbers from current page
# =============================================================================
def extract_numbers_from_page(driver) -> list[dict]:
    """Extract all numbers from the currently visible datagrid page."""
    numbers = []
    content = driver.find_element(By.ID, "dtgavailablenumbers_content")
    labels = content.find_elements(
        By.CSS_SELECTOR, ".ui-datagrid-column label.ui-outputlabel"
    )

    for label in labels:
        span = label.find_element(By.CSS_SELECTOR, "span.ui-outputlabel-label")
        number_text = span.text.strip()
        if not number_text:
            continue

        available = get_availability(label)
        category = categorize_number(number_text)

        numbers.append({
            "number": number_text,
            "available": available,
            "category": category,
        })

    return numbers


# =============================================================================
# CSV helpers — incremental write & resume
# =============================================================================
CSV_FIELDS = ["number", "available", "category"]


def get_resume_page() -> int:
    """
    Check the existing CSV to determine which page to resume from.
    Returns the 1-based page number to start scraping from.
    If no CSV exists or it's empty, returns 1.
    """
    if not OUTPUT_FILE.exists():
        return 1

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row_count = sum(1 for _ in reader)

        if row_count == 0:
            return 1

        # Each page has NUMBERS_PER_PAGE numbers
        completed_pages = row_count // NUMBERS_PER_PAGE
        resume_page = completed_pages + 1
        print(f"  Found existing CSV with {row_count} numbers ({completed_pages} full pages).")
        print(f"  Resuming from page {resume_page}.")
        return resume_page
    except Exception as e:
        print(f"  Warning: Could not read existing CSV ({e}), starting fresh.")
        return 1


def append_to_csv(rows: list[dict]):
    """Append rows to the CSV file, creating it with headers if it doesn't exist."""
    file_exists = OUTPUT_FILE.exists() and OUTPUT_FILE.stat().st_size > 0

    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def read_existing_numbers() -> list[dict]:
    """Read all existing numbers from the CSV (for summary stats)."""
    if not OUTPUT_FILE.exists():
        return []
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


# =============================================================================
# Get total page count from paginator
# =============================================================================
def get_total_pages(driver) -> int:
    """Parse the paginator text '(X of N)' to get N."""
    try:
        paginator = driver.find_element(
            By.CSS_SELECTOR, "#dtgavailablenumbers_paginator_top .ui-paginator-current"
        )
        text = paginator.text.strip()
        match = re.search(r"\((\d+)\s+of\s+(\d+)\)", text)
        if match:
            return int(match.group(2))
    except Exception as e:
        print(f"  Warning: Could not parse total pages: {e}")
    return 1


# =============================================================================
# Navigate to a specific page in the paginator
# =============================================================================
def navigate_to_page(driver, target_page: int, total_pages: int):
    """
    Navigate to a specific page by clicking Next repeatedly.
    PrimeFaces paginators don't support direct page jumps easily,
    so we click through sequentially.
    """
    current = get_current_page(driver)
    if current == target_page:
        return

    print(f"  Fast-forwarding from page {current} to page {target_page}...")
    while current < target_page:
        next_btn = driver.find_element(
            By.CSS_SELECTOR,
            "#dtgavailablenumbers_paginator_top .ui-paginator-next",
        )
        btn_classes = next_btn.get_attribute("class") or ""
        if "ui-state-disabled" in btn_classes:
            print(f"  ⚠ Next button disabled at page {current}")
            break

        next_btn.click()
        time.sleep(PAGE_DELAY)

        # Wait for page to update
        for _ in range(10):
            new_page = get_current_page(driver)
            if new_page > current:
                current = new_page
                break
            time.sleep(1)

        if current % 10 == 0:
            print(f"    ... at page {current}")


def get_current_page(driver) -> int:
    """Get the current page number from the paginator."""
    try:
        paginator = driver.find_element(
            By.CSS_SELECTOR, "#dtgavailablenumbers_paginator_top .ui-paginator-current"
        )
        text = paginator.text.strip()
        match = re.search(r"\((\d+)\s+of\s+(\d+)\)", text)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 1


# =============================================================================
# Main scraping flow
# =============================================================================
def main():
    print("=" * 60)
    print("  Parivahan Fancy Number Fetcher")
    print("=" * 60)
    print()

    # Check for resume
    start_page = get_resume_page()
    print()

    # ── Launch Chrome ──────────────────────────────────────────
    print("[1/5] Launching Chrome browser...")
    chrome_options = Options()
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-infobars")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    wait = WebDriverWait(driver, 30)

    try:
        # ── Navigate to login page ────────────────────────────
        print(f"[2/5] Navigating to {LOGIN_URL}")
        driver.get(LOGIN_URL)
        time.sleep(2)

        print()
        print("  ┌─────────────────────────────────────────────────┐")
        print("  │  Please LOG IN manually in the browser window.  │")
        print("  │  Complete: username, password, OTP, captcha.    │")
        print("  │                                                 │")
        print("  │  After login, come back here and press ENTER.   │")
        print("  └─────────────────────────────────────────────────┘")
        print()
        input("  >>> Press ENTER after you have logged in... ")
        print()

        # ── Check if we're on the home page ───────────────────
        current_url = driver.current_url
        if "applicanthome" not in current_url:
            print("  Navigating to home page...")
            driver.get(HOME_URL)
            time.sleep(3)

        # ── Select Dropdowns ──────────────────────────────────
        print("[3/5] Selecting vehicle details...")
        print()

        print("  Selecting Fuel Type...")
        select_primefaces_dropdown(driver, wait, "sel_fuel_type", FUEL_TYPE)
        time.sleep(DROPDOWN_DELAY)

        print("  Selecting Vehicle Category...")
        select_primefaces_dropdown(driver, wait, "ib_stateb", "LIGHT MOTOR VEHICLE")
        time.sleep(DROPDOWN_DELAY)

        print("  Selecting Vehicle Series...")
        select_primefaces_dropdown(driver, wait, "ib_Veh_Seri", VEHICLE_SERIES)
        time.sleep(DROPDOWN_DELAY)

        print()
        print("  All dropdowns selected. Waiting for page to settle...")
        time.sleep(3)

        # ── Click "Registration Number Status" button ─────────
        print("[4/5] Clicking 'Registration Number Status' button...")
        btn = wait.until(EC.element_to_be_clickable((By.ID, "checknumberid")))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
        time.sleep(1)
        btn.click()

        print("  Waiting for numbers to load...")
        time.sleep(5)
        wait.until(EC.presence_of_element_located((By.ID, "dtgavailablenumbers_content")))
        print("  ✓ Numbers grid loaded!")
        print()

        # ── Scrape all pages ──────────────────────────────────
        print("[5/5] Scraping all pages...")
        total_pages = get_total_pages(driver)
        print(f"  Total pages: {total_pages}")

        if start_page > 1:
            navigate_to_page(driver, start_page, total_pages)

        print()

        fetched_this_run = 0
        for page_num in range(start_page, total_pages + 1):
            # Extract numbers from current page
            page_numbers = extract_numbers_from_page(driver)

            # Write to CSV immediately
            append_to_csv(page_numbers)
            fetched_this_run += len(page_numbers)

            available_count = sum(1 for n in page_numbers if n["available"] == "Yes")
            booked_count = sum(1 for n in page_numbers if n["available"] == "No")
            print(
                f"  Page {page_num:3d}/{total_pages} — "
                f"{len(page_numbers):3d} numbers "
                f"(✓{available_count} avail, ✗{booked_count} booked) — "
                f"Total this run: {fetched_this_run}"
            )

            # Navigate to next page
            if page_num < total_pages:
                try:
                    next_btn = driver.find_element(
                        By.CSS_SELECTOR,
                        "#dtgavailablenumbers_paginator_top .ui-paginator-next",
                    )
                    btn_classes = next_btn.get_attribute("class") or ""
                    if "ui-state-disabled" in btn_classes:
                        print("  ⚠ Next button disabled — stopping.")
                        break

                    next_btn.click()
                    time.sleep(PAGE_DELAY)

                    # Wait for page to actually update
                    for _ in range(10):
                        try:
                            pag = driver.find_element(
                                By.CSS_SELECTOR,
                                "#dtgavailablenumbers_paginator_top .ui-paginator-current",
                            )
                            if f"({page_num + 1} of" in pag.text.strip():
                                break
                        except Exception:
                            pass
                        time.sleep(1)

                except Exception as e:
                    print(f"  ⚠ Error navigating to page {page_num + 1}: {e}")
                    print("  Retrying...")
                    time.sleep(5)
                    try:
                        next_btn = driver.find_element(
                            By.CSS_SELECTOR,
                            "#dtgavailablenumbers_paginator_top .ui-paginator-next",
                        )
                        next_btn.click()
                        time.sleep(PAGE_DELAY + 2)
                    except Exception as e2:
                        print(f"  ✗ Retry failed: {e2} — saving progress.")
                        break

        # ── Summary ───────────────────────────────────────────
        print()
        print(f"  ✓ Done! Fetched {fetched_this_run} numbers this run.")
        print(f"  ✓ CSV: {OUTPUT_FILE}")
        print()

        # Read full CSV for summary
        all_numbers = read_existing_numbers()
        total = len(all_numbers)
        avail = sum(1 for n in all_numbers if n.get("available") == "Yes")
        booked = sum(1 for n in all_numbers if n.get("available") == "No")
        unknown = total - avail - booked

        print("  ── Summary ──────────────────────────────────────")
        print(f"  Total numbers:  {total}")
        print(f"  Available:      {avail}")
        print(f"  Booked:         {booked}")
        if unknown:
            print(f"  Unknown:        {unknown}")
        print()

        categories = {}
        for entry in all_numbers:
            cat = entry.get("category", "OTHER")
            categories[cat] = categories.get(cat, 0) + 1

        print("  ── Category Breakdown ────────────────────────────")
        for cat in sorted(categories.keys()):
            print(f"   {cat:12s}  {categories[cat]:5d} numbers")
        print()

        input("  >>> Press ENTER to close the browser and exit... ")

    except KeyboardInterrupt:
        print(f"\n  Interrupted! Progress saved to: {OUTPUT_FILE}")
        print(f"  Re-run the script to resume from where you left off.")
    except Exception as e:
        print(f"\n  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        print(f"\n  Progress saved to: {OUTPUT_FILE}")
        print(f"  Re-run the script to resume from where you left off.")
        input("\n  >>> Press ENTER to close the browser and exit... ")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
