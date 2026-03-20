# 🚗 Parivahan Fancy Number Fetcher

Fetch all available & booked fancy car registration numbers from [Parivahan](https://fancy.parivahan.gov.in/) and export them to CSV — with an interactive CLI that lets you pick which Delhi vehicle series to scrape.

## ✨ Features

- **Interactive Series Selector** — Pick one, many, or all 13 Delhi vehicle series from a beautiful checkbox prompt
- **One Login, Multiple Series** — Log in once; the tool loops through all your selected series automatically
- **Rich CLI Output** — Styled banners, progress bars, and summary tables powered by [Rich](https://github.com/Textualize/rich)
- **Incremental CSV Saving** — Results saved after every page, so nothing is lost if interrupted
- **Resume Support** — Re-run the tool and it picks up right where it left off
- **Pattern Categorization** — Numbers are auto-categorized (XXXX, XYYY, XXYY, XYXY, SEQUENTIAL, etc.)

## 📋 Prerequisites

- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** — Fast Python package manager
- **Google Chrome** — The tool uses Selenium with ChromeDriver (auto-installed)

## 🚀 Getting Started

### 1. Clone the repo

```bash
git clone https://github.com/devcrypted/mparivahan-car-fancy-number-fetcher.git
cd mparivahan-car-fancy-number-fetcher
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Run the tool

```bash
uv run python main.py
```

## 🎯 How It Works

1. **Select Series** — An interactive prompt appears. Use `Space` to toggle series, `Enter` to confirm. There's a "Select All" option at the top.

2. **Login** — Chrome opens the Parivahan website. Log in manually (username, password, OTP, captcha). Come back to the terminal and press `Enter`.

3. **Scraping** — The tool automatically selects dropdowns, clicks through pages, and extracts every number. A progress bar shows real-time status.

4. **Results** — CSVs are saved to the `OP/` folder, one per series (e.g., `OP/DL5CY_numbers.csv`).

## 📊 CSV Output Format

Each CSV contains these columns:

| Column | Example | Description |
| -------- | -------- | ------------- |
| `series` | `DL5CY` | Vehicle series code |
| `number` | `0102` | 4-digit registration number |
| `final_number` | `DL5CY0102` | Full registration number (series + number) |
| `available` | `Yes` / `No` | Whether the number is available to book |
| `category` | `XXYY` | Number pattern category |

### Pattern Categories

| Pattern | Example | Description |
| --------- | --------- | ------------- |
| `XXXX` | 1111 | All same digits |
| `XYYY` | 1222 | First different, last 3 same |
| `YYYX` | 2221 | First 3 same, last different |
| `XXYY` | 1122 | First pair + second pair |
| `XYXY` | 1212 | Alternating pair |
| `XYYX` | 1221 | Palindrome |
| `XXYZ` | 1134 | First 2 same, rest different |
| `XYZZ` | 1233 | Last 2 same, rest different |
| `SEQUENTIAL` | 1234 | Ascending/descending consecutive |
| `OTHER` | 1357 | No special pattern |

## 🗂 Available Series

The tool supports all current Delhi vehicle series:

`DL1CAK` · `DL2CBG` · `DL3CDE` · `DL4CBF` · `DL5CY` · `DL6CT` · `DL7CY` · `DL8CBL` · `DL9CBM` · `DL10DB` · `DL11CG` · `DL12DA` · `DL14CM`

## ⚙️ Configuration

Timing constants in `main.py` you can tweak if the site is slow:

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `PAGE_DELAY` | `3` | Seconds between page navigations |
| `DROPDOWN_DELAY` | `4` | Seconds after selecting a dropdown |

## 📝 License

MIT
