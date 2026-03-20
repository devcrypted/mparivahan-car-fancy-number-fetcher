"""
Parivahan Fancy Number Fetcher
================================
Fetches all available/booked registration numbers from the Parivahan
fancy number website and exports them to CSV with pattern categories.

Features:
- Beautiful CLI with Typer + Rich
- Interactive multi-select for vehicle series (with Select All)
- Adds series & final_number columns to CSV
- Manual login (you handle username, password, OTP, captcha)
- Incremental CSV writing (saves after every page)
- Resume support (detects existing CSV and skips already-fetched pages)
- Pattern categorization (XXXX, XYYY, YYYX, XXYY, etc.)
"""

import csv
import re
import time
from pathlib import Path

import typer
from InquirerPy import inquirer
from InquirerPy.separator import Separator
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ─── Typer & Rich setup ─────────────────────────────────────────────────────
app = typer.Typer(
    name="fetch-numbers",
    help="🚗 Fetch fancy car registration numbers from Parivahan",
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()

# ─── Constants ───────────────────────────────────────────────────────────────
FUEL_TYPE = "PETROL"
VEHICLE_CATEGORY = "LMV"
LOGIN_URL = "https://fancy.parivahan.gov.in/"
HOME_URL = "https://fancy.parivahan.gov.in/fancy/faces/app/applicanthome.xhtml"

ALL_SERIES = [
    "DL1CAK",
    "DL2CBG",
    "DL3CDE",
    "DL4CBF",
    "DL5CY",
    "DL6CT",
    "DL7CY",
    "DL8CBL",
    "DL9CBM",
    "DL10DB",
    "DL11CG",
    "DL12DA",
    "DL14CM",
]

SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR / "OP"

PAGE_DELAY = 3
DROPDOWN_DELAY = 4
NUMBERS_PER_PAGE = 100

CSV_FIELDS = ["series", "number", "final_number", "available", "category"]


# ─── Number Pattern Categorization ──────────────────────────────────────────
def categorize_number(num_str: str) -> str:
    """Categorize a 4-digit number string into a pattern category."""
    if len(num_str) != 4 or not num_str.isdigit():
        return "OTHER"

    a, b, c, d = num_str

    if a == b == c == d:
        return "XXXX"
    if b == c == d and a != b:
        return "XYYY"
    if a == b == c and d != a:
        return "YYYX"
    if a == b and c == d and a != c:
        return "XXYY"
    if a == c and b == d and a != b:
        return "XYXY"
    if a == d and b == c and a != b:
        return "XYYX"
    if a == b and c != d and c != a and d != a:
        return "XXYZ"
    if c == d and a != b and a != c and b != c:
        return "XYZZ"

    digits = [int(ch) for ch in num_str]
    diffs = [digits[i + 1] - digits[i] for i in range(3)]
    if diffs == [1, 1, 1] or diffs == [-1, -1, -1]:
        return "SEQUENTIAL"

    return "OTHER"


# ─── Availability Detection ─────────────────────────────────────────────────
def get_availability(label_element) -> str:
    """Determine if a number is available or booked by background color."""
    bg = (label_element.value_of_css_property("background-color") or "").lower().replace(" ", "")

    if "228,248,231" in bg:
        return "Yes"
    if "255,137,0" in bg:
        return "No"

    style = (label_element.get_attribute("style") or "").lower()
    if "e4f8e7" in style:
        return "Yes"
    if "ff8900" in style:
        return "No"

    return "Unknown"


# ─── PrimeFaces Dropdown Helper ──────────────────────────────────────────────
def select_primefaces_dropdown(driver, wait, dropdown_id: str, value_text: str):
    """Select a value from a PrimeFaces SelectOneMenu dropdown."""
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
            return True

    for item in items:
        if value_text.upper() in item.text.strip().upper():
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", item)
            time.sleep(0.3)
            item.click()
            return True

    return False


# ─── Page Extraction ─────────────────────────────────────────────────────────
def extract_numbers_from_page(driver, series: str) -> list[dict]:
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
            "series": series,
            "number": number_text,
            "final_number": f"{series}{number_text}",
            "available": available,
            "category": category,
        })

    return numbers


# ─── CSV Helpers ─────────────────────────────────────────────────────────────
def get_output_file(series: str) -> Path:
    return OUTPUT_DIR / f"{series}_numbers.csv"


def get_resume_page(series: str) -> int:
    """Check the existing CSV to determine which page to resume from."""
    output_file = get_output_file(series)
    if not output_file.exists():
        return 1

    try:
        with open(output_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row_count = sum(1 for _ in reader)

        if row_count == 0:
            return 1

        completed_pages = row_count // NUMBERS_PER_PAGE
        resume_page = completed_pages + 1
        console.print(f"  Found existing CSV with [cyan]{row_count}[/] numbers ([cyan]{completed_pages}[/] full pages).")
        console.print(f"  Resuming from page [cyan]{resume_page}[/].")
        return resume_page
    except Exception:
        return 1


def append_to_csv(series: str, rows: list[dict]):
    """Append rows to the CSV file, creating it with headers if needed."""
    output_file = get_output_file(series)
    file_exists = output_file.exists() and output_file.stat().st_size > 0

    with open(output_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def read_existing_numbers(series: str) -> list[dict]:
    """Read all existing numbers from the CSV (for summary stats)."""
    output_file = get_output_file(series)
    if not output_file.exists():
        return []
    try:
        with open(output_file, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


# ─── Paginator Helpers ───────────────────────────────────────────────────────
def get_total_pages(driver) -> int:
    try:
        paginator = driver.find_element(
            By.CSS_SELECTOR, "#dtgavailablenumbers_paginator_top .ui-paginator-current"
        )
        text = paginator.text.strip()
        match = re.search(r"\((\d+)\s+of\s+(\d+)\)", text)
        if match:
            return int(match.group(2))
    except Exception:
        pass
    return 1


def get_current_page(driver) -> int:
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


def navigate_to_page(driver, target_page: int):
    """Navigate to a specific page by clicking Next repeatedly."""
    current = get_current_page(driver)
    if current == target_page:
        return

    console.print(f"  Fast-forwarding from page [cyan]{current}[/] → [cyan]{target_page}[/]...")
    while current < target_page:
        next_btn = driver.find_element(
            By.CSS_SELECTOR,
            "#dtgavailablenumbers_paginator_top .ui-paginator-next",
        )
        btn_classes = next_btn.get_attribute("class") or ""
        if "ui-state-disabled" in btn_classes:
            break

        next_btn.click()
        time.sleep(PAGE_DELAY)

        for _ in range(10):
            new_page = get_current_page(driver)
            if new_page > current:
                current = new_page
                break
            time.sleep(1)


# ─── Scrape a Single Series ──────────────────────────────────────────────────
def scrape_series(driver, wait, series: str, series_idx: int, total_series: int):
    """Scrape all pages for a single vehicle series."""
    console.print()
    console.rule(f"[bold cyan]Series {series_idx}/{total_series}: {series}[/]")

    start_page = get_resume_page(series)

    # Select dropdowns
    console.print("  Selecting [yellow]Fuel Type[/]...")
    select_primefaces_dropdown(driver, wait, "sel_fuel_type", FUEL_TYPE)
    time.sleep(DROPDOWN_DELAY)

    console.print("  Selecting [yellow]Vehicle Category[/]...")
    select_primefaces_dropdown(driver, wait, "ib_stateb", "LIGHT MOTOR VEHICLE")
    time.sleep(DROPDOWN_DELAY)

    console.print("  Selecting [yellow]Vehicle Series[/] → [bold green]{series}[/]...".format(series=series))
    select_primefaces_dropdown(driver, wait, "ib_Veh_Seri", series)
    time.sleep(DROPDOWN_DELAY)

    console.print("  Waiting for page to settle...")
    time.sleep(3)

    # Click "Registration Number Status"
    console.print("  Clicking [bold]Registration Number Status[/]...")
    btn = wait.until(EC.element_to_be_clickable((By.ID, "checknumberid")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
    time.sleep(1)
    btn.click()

    console.print("  Waiting for numbers to load...")
    time.sleep(5)
    wait.until(EC.presence_of_element_located((By.ID, "dtgavailablenumbers_content")))
    console.print("  [bold green]✓[/] Numbers grid loaded!")

    # Scrape
    total_pages = get_total_pages(driver)
    console.print(f"  Total pages: [cyan]{total_pages}[/]")

    if start_page > 1:
        navigate_to_page(driver, start_page)

    fetched_this_run = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            f"  [cyan]{series}[/]",
            total=total_pages,
            completed=start_page - 1,
        )

        for page_num in range(start_page, total_pages + 1):
            page_numbers = extract_numbers_from_page(driver, series)
            append_to_csv(series, page_numbers)
            fetched_this_run += len(page_numbers)

            avail = sum(1 for n in page_numbers if n["available"] == "Yes")
            booked = sum(1 for n in page_numbers if n["available"] == "No")

            progress.update(task, completed=page_num, description=(
                f"  [cyan]{series}[/] pg {page_num}/{total_pages} "
                f"([green]✓{avail}[/] [red]✗{booked}[/]) — "
                f"total: {fetched_this_run}"
            ))

            if page_num < total_pages:
                try:
                    next_btn = driver.find_element(
                        By.CSS_SELECTOR,
                        "#dtgavailablenumbers_paginator_top .ui-paginator-next",
                    )
                    btn_classes = next_btn.get_attribute("class") or ""
                    if "ui-state-disabled" in btn_classes:
                        console.print("  [yellow]⚠[/] Next button disabled — stopping.")
                        break

                    next_btn.click()
                    time.sleep(PAGE_DELAY)

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
                    console.print(f"  [yellow]⚠[/] Navigation error: {e}")
                    console.print("  Retrying...")
                    time.sleep(5)
                    try:
                        next_btn = driver.find_element(
                            By.CSS_SELECTOR,
                            "#dtgavailablenumbers_paginator_top .ui-paginator-next",
                        )
                        next_btn.click()
                        time.sleep(PAGE_DELAY + 2)
                    except Exception:
                        console.print("  [red]✗[/] Retry failed — saving progress.")
                        break

    # Series summary
    output_file = get_output_file(series)
    console.print(f"\n  [bold green]✓[/] Done! Fetched [cyan]{fetched_this_run}[/] numbers this run.")
    console.print(f"  [bold green]✓[/] CSV: [link=file://{output_file}]{output_file}[/]")

    return fetched_this_run


def print_series_summary(series: str):
    """Print a Rich table summary for a completed series."""
    all_numbers = read_existing_numbers(series)
    if not all_numbers:
        return

    total = len(all_numbers)
    avail = sum(1 for n in all_numbers if n.get("available") == "Yes")
    booked = sum(1 for n in all_numbers if n.get("available") == "No")
    unknown = total - avail - booked

    table = Table(title=f"Summary — {series}", show_header=True, header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("Total Numbers", str(total))
    table.add_row("Available", str(avail))
    table.add_row("Booked", str(booked))
    if unknown:
        table.add_row("Unknown", str(unknown))

    console.print()
    console.print(table)

    # Category breakdown
    categories: dict[str, int] = {}
    for entry in all_numbers:
        cat = entry.get("category", "OTHER")
        categories[cat] = categories.get(cat, 0) + 1

    cat_table = Table(title=f"Category Breakdown — {series}", show_header=True, header_style="bold magenta")
    cat_table.add_column("Category", style="cyan")
    cat_table.add_column("Count", justify="right", style="green")
    for cat in sorted(categories.keys()):
        cat_table.add_row(cat, str(categories[cat]))

    console.print(cat_table)


# ─── Interactive Series Selection ────────────────────────────────────────────
def prompt_series_selection() -> list[str]:
    """Show an interactive multi-select prompt for vehicle series."""
    choices = [
        {"name": "Select All", "value": "__ALL__"},
        Separator("─" * 30),
        *[{"name": s, "value": s} for s in ALL_SERIES],
    ]

    selected = inquirer.checkbox(
        message="Select vehicle series to fetch:",
        choices=choices,
        instruction="(Space to toggle, Enter to confirm)",
        validate=lambda result: len(result) > 0,
        invalid_message="You must select at least one series.",
        transformer=lambda result: f"{len(result)} series selected",
    ).execute()

    if "__ALL__" in selected:
        return list(ALL_SERIES)

    return selected


# ─── Main CLI Command ────────────────────────────────────────────────────────
@app.command()
def fetch():
    """
    🚗 Fetch fancy car registration numbers from Parivahan.

    Launches a browser, lets you log in manually, then scrapes
    all number plates for the selected vehicle series.
    """

    # Banner
    banner = Text()
    banner.append("🚗 Parivahan Fancy Number Fetcher\n", style="bold cyan")
    banner.append("   Fetch & categorize Delhi vehicle registration numbers", style="dim")
    console.print(Panel(banner, border_style="cyan", padding=(1, 2)))
    console.print()

    # ── Interactive series selection ──────────────────────────
    selected_series = prompt_series_selection()

    if not selected_series:
        console.print("[red]No series selected. Exiting.[/]")
        raise typer.Exit()

    console.print()
    series_list = ", ".join(f"[bold green]{s}[/]" for s in selected_series)
    console.print(f"  Selected: {series_list}")
    console.print()

    # ── Ensure output directory exists ────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Launch Chrome ─────────────────────────────────────────
    console.print("[bold]Launching Chrome browser...[/]")
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
        # ── Navigate & login ──────────────────────────────────
        console.print(f"Navigating to [link={LOGIN_URL}]{LOGIN_URL}[/]")
        driver.get(LOGIN_URL)
        time.sleep(2)

        console.print()
        console.print(
            Panel(
                "[bold yellow]Please LOG IN manually in the browser window.[/]\n"
                "Complete: username, password, OTP, captcha.\n\n"
                "[dim]After login, come back here and press ENTER.[/]",
                title="🔐 Manual Login Required",
                border_style="yellow",
                padding=(1, 2),
            )
        )

        input("  >>> Press ENTER after you have logged in... ")
        console.print()

        current_url = driver.current_url
        if "applicanthome" not in current_url:
            console.print("  Navigating to home page...")
            driver.get(HOME_URL)
            time.sleep(3)

        # ── Process each selected series ──────────────────────
        total_fetched = 0
        for idx, series in enumerate(selected_series, 1):
            try:
                count = scrape_series(driver, wait, series, idx, len(selected_series))
                total_fetched += count
                print_series_summary(series)
            except KeyboardInterrupt:
                console.print(f"\n  [yellow]Interrupted during {series}![/] Progress saved.")
                break
            except Exception as e:
                console.print(f"\n  [red]✗ Error on {series}: {e}[/]")
                import traceback
                traceback.print_exc()
                console.print(f"  Progress saved. Continuing to next series...")
                continue

            # Navigate back to home for next series
            if idx < len(selected_series):
                console.print("\n  Navigating back for next series...")
                driver.get(HOME_URL)
                time.sleep(3)

        # ── Final summary ─────────────────────────────────────
        console.print()
        console.print(Panel(
            f"[bold green]✓ All done![/]\n"
            f"Fetched [cyan]{total_fetched}[/] numbers across "
            f"[cyan]{len(selected_series)}[/] series.\n"
            f"CSVs saved to: [bold]{OUTPUT_DIR}[/]",
            title="🏁 Complete",
            border_style="green",
            padding=(1, 2),
        ))

        input("\n  >>> Press ENTER to close the browser and exit... ")

    except KeyboardInterrupt:
        console.print(f"\n  [yellow]Interrupted![/] Progress saved to: [bold]{OUTPUT_DIR}[/]")
        console.print("  Re-run the script to resume from where you left off.")
    except Exception as e:
        console.print(f"\n  [red]✗ Error: {e}[/]")
        import traceback
        traceback.print_exc()
        console.print(f"\n  Progress saved to: [bold]{OUTPUT_DIR}[/]")
        console.print("  Re-run the script to resume from where you left off.")
        input("\n  >>> Press ENTER to close the browser and exit... ")
    finally:
        driver.quit()


if __name__ == "__main__":
    app()
