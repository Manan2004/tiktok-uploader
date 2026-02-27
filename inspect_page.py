#!/usr/bin/env python3
"""
Opens TikTok upload page with your cookies and prints all interactive
elements — helps find the correct selectors for schedule toggle and popups.
"""
import time
from pathlib import Path
from tiktok_uploader.auth import AuthBackend
from tiktok_uploader.browsers import get_browser

COOKIES_FILE = Path("cookies.txt")
UPLOAD_URL = "https://www.tiktok.com/creator-center/upload?lang=en"

auth = AuthBackend(cookies=str(COOKIES_FILE))
page = get_browser("chrome", headless=False)
page = auth.authenticate_agent(page)

print("Navigating to upload page…")
page.goto(UPLOAD_URL)
page.wait_for_selector("#root", timeout=60000)
time.sleep(5)  # let the page fully settle

# Dump all buttons and inputs visible on screen
print("\n=== BUTTONS ===")
for btn in page.locator("button:visible, [role='button']:visible").all()[:20]:
    try:
        print(f"  text={btn.inner_text()!r:40s}  class={btn.get_attribute('class')!r}")
    except Exception:
        pass

print("\n=== INPUTS / CHECKBOXES ===")
for inp in page.locator("input:visible, [role='switch']:visible").all()[:20]:
    try:
        print(
            f"  type={inp.get_attribute('type')!r}  id={inp.get_attribute('id')!r}  class={inp.get_attribute('class')!r}"
        )
    except Exception:
        pass

print("\n=== DIVS with 'schedule' in class/text ===")
for el in page.locator(
    "//*[contains(translate(@class,'SCHEDULE','schedule'),'schedule') or contains(translate(text(),'SCHEDULE','schedule'),'schedule')]"
).all()[:10]:
    try:
        print(
            f"  tag={el.evaluate('e=>e.tagName')!r}  text={el.inner_text()[:60]!r}  class={el.get_attribute('class')!r}"
        )
    except Exception:
        pass

print("\nDone — keeping browser open for 60s so you can inspect manually…")
time.sleep(60)
page.context.browser.close()
