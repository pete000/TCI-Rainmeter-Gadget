import sys, os
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, os, re, json, traceback
from datetime import datetime

# ================= CONFIGURATION =================
USERNAME       = "YOUR_TCI_USERNAME""          # <-- CHANGE
PASSWORD       = "YOUR_TCI_PASSWORD"          # <-- CHANGE
TCI_URL        = "https://internet.tci.ir/panel/"
OUTPUT_FILE    = r"C:\RainmeterTCI\tci_data.txt"
USERSCRIPT     = r"C:\RainmeterTCI\tci-captcha-solver.user.js"
HEADLESS       = True        # set False the first time to debug
MAX_ATTEMPTS   = 3
# =================================================

PERSIAN_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٫", "0123456789.")
def to_latin(s: str) -> str:
    return s.translate(PERSIAN_MAP)

def clean_number(s: str) -> str:
    """Keep only digits and one decimal point."""
    s = to_latin(s).strip()
    s = re.sub(r"[^\d.]", "", s)
    if s.count(".") > 1:
        parts = s.split(".")
        s = parts[0] + "." + "".join(parts[1:])
    return s or "0"

def find_first(driver, selectors, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        for sel in selectors:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    return el
            except Exception:
                pass
        time.sleep(0.4)
    raise RuntimeError(f"None of these selectors matched: {selectors}")

def write_output(values: dict, status="ok"):
    """Atomic write — prevents Rainmeter from reading a half-written file."""
    line = "|".join([
        values.get("vol_remaining", "0"),
        values.get("vol_total",     "0"),
        values.get("days_remaining","0"),
        values.get("days_total",    "0"),
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        status,
    ])
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    tmp = OUTPUT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(line)
    os.replace(tmp, OUTPUT_FILE)
    print(f"Wrote: {line}")

def parse_stats(html: str) -> dict:
    # Target the volume pie specifically
    volume_block = re.search(
        r'<div class="chart">.*?<div class="pie-volume-1 percentage"[^>]*>.*?<div class="percent">.*?<span dir="ltr">([^<]+)</span>.*?<hr>.*?<span dir="ltr">([^<]+)</span>',
        html,
        re.DOTALL | re.IGNORECASE
    )
    if not volume_block:
        raise RuntimeError("Could not find volume pie block in HTML.")

    vol_remaining_raw, vol_total_raw = volume_block.groups()

    # Target the days pie specifically
    # The days pie is the one WITHOUT data-max="87040" (or simply the first pie-volume-1 on the page)
    days_block = re.search(
        r'<div class="pie-volume-1 percentage"[^>]*>.*?<div class="percent">\s*<span>([^<]+)</span>\s*<hr[^>]*>\s*<span>([^<]+)</span>',
        html,
        re.DOTALL | re.IGNORECASE
    )
    if not days_block:
        raise RuntimeError("Could not find days pie block in HTML.")

    days_remaining_raw, days_total_raw = days_block.groups()

    return {
        "vol_remaining":  clean_number(vol_remaining_raw),
        "vol_total":      clean_number(vol_total_raw),
        "days_remaining": clean_number(days_remaining_raw),
        "days_total":     clean_number(days_total_raw),
    }
def main():
    if not os.path.exists(USERSCRIPT):
        raise FileNotFoundError(f"Missing userscript: {USERSCRIPT}")

    options = uc.ChromeOptions()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--no-proxy-server")           # TCI is local → bypass proxy
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = uc.Chrome(options=options, version_main=156, driver_executable_path=r"C:\RainmeterTCI\chromedriver.exe")
    try:
        with open(USERSCRIPT, "r", encoding="utf-8") as f:
            userscript_code = f.read()

        # Inject userscript BEFORE the page loads so it catches the audio CAPTCHA
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": userscript_code},
        )

        last_err = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                print(f"[Attempt {attempt}/{MAX_ATTEMPTS}] Opening {TCI_URL}")
                driver.get(TCI_URL)
                
                # Wait for page to load
                print("Waiting for page to load...")
                time.sleep(10)

                # --- DISMISS THE MODAL VIA JAVASCRIPT ---
                print("Attempting to dismiss notice modal...")
                driver.execute_script("""
                    var buttons = document.querySelectorAll('button, a');
                    for (var i = 0; i < buttons.length; i++) {
                        if (buttons[i].innerText.trim() === 'بستن') {
                            buttons[i].click();
                            break;
                        }
                    }
                """)
                time.sleep(2) # Wait for modal close animation
                # ----------------------------------------

                # Wait for fields using the robust find_first function
                user_el = find_first(driver, [
                    "input[name='username']", "input[name='user']",
                    "input[id='username']",   "input[id='user']",
                    "input[name='user_name']",
                ])
                pass_el = find_first(driver, [
                    "input[name='password']", "input[id='password']",
                    "input[type='password']",
                ])
                submit_el = find_first(driver, [
                    "button[type='submit']", "input[type='submit']",
                    "button.uk-button-primary", "button[onclick*='login']",
                    "button" # absolute fallback
                ])

                # Give the userscript time to solve + fill the CAPTCHA
                print("Waiting for CAPTCHA solver...")
                time.sleep(8)

                user_el.clear(); user_el.send_keys(USERNAME)
                pass_el.clear(); pass_el.send_keys(PASSWORD)
                
                # Use JS to click the button (bypasses any leftover invisible overlays)
                driver.execute_script("arguments[0].click();", submit_el)

                # Wait for dashboard to load
                print("Waiting for dashboard to load...")
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.pie-volume-1"))
                )
                time.sleep(4) # Wait for JS to render the actual numbers
                print("Dashboard loaded. Parsing...")
                stats = parse_stats(driver.page_source)
                write_output(stats, "ok")
                return  # Success

            except Exception as e:
                last_err = e
                print(f"Attempt {attempt} failed: {e}")
                traceback.print_exc()
                time.sleep(4)

        raise RuntimeError(f"All {MAX_ATTEMPTS} attempts failed. Last: {last_err}")
    except Exception as e:
        print(f"FATAL: {e}")
        write_output({}, f"error:{str(e)[:60].replace('|','/').replace(chr(10),' ')}")
    finally:
        try: driver.quit()
        except Exception: pass

if __name__ == "__main__":
    main()