#!/usr/bin/env python3
"""
Inspect the TikTok schedule UI — dumps HTML after clicking Schedule radio.
Usage: uv run python inspect_schedule.py videos/IMG_0109.mp4
"""
import sys, time, asyncio
from pathlib import Path
from playwright.sync_api import sync_playwright

COOKIES_FILE = "cookies.txt"


def load_cookies(path):
    cookies = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path_c, secure, expires, name, value = parts[:7]
            cookies.append(
                {
                    "domain": domain.lstrip("."),
                    "path": path_c,
                    "secure": secure.upper() == "TRUE",
                    "name": name,
                    "value": value,
                    "sameSite": "None",
                }
            )
    return cookies


def main():
    video = Path(sys.argv[1] if len(sys.argv) > 1 else "videos/IMG_0109.mp4")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        ctx = browser.new_context()
        ctx.add_cookies(load_cookies(COOKIES_FILE))
        page = ctx.new_page()

        page.goto(
            "https://www.tiktok.com/tiktok-studio/upload", wait_until="domcontentloaded"
        )
        time.sleep(3)

        # Dismiss cookie banner
        try:
            page.locator("tiktok-cookie-banner").locator(
                "div.button-wrapper"
            ).last.click(timeout=3000)
        except Exception:
            pass

        # Upload video
        file_input = page.locator("input[type=file]").first
        file_input.set_input_files(str(video))
        print("Waiting for video to upload…")
        time.sleep(10)

        # Dismiss popup
        try:
            btn = page.locator(
                "//button[contains(translate(., 'GOTIQUK', 'gotiquk'), 'got it')] | //button[contains(translate(., 'GOTIQUK', 'gotiquk'), 'ok')] | //div[contains(@class,'modal')]//button"
            ).first
            if btn.is_visible(timeout=3000):
                btn.click()
        except Exception:
            pass

        # Click Schedule
        for sel in [
            "label:has-text('Schedule')",
            "xpath=//label[contains(normalize-space(.), 'Schedule')]",
            "xpath=//span[normalize-space(text())='Schedule']",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    print(f"Clicked: {sel}")
                    break
            except Exception:
                continue

        time.sleep(1)

        # Take screenshot
        page.screenshot(path="schedule_screenshot.png", full_page=False)
        print("Screenshot saved: schedule_screenshot.png")

        # Dump all visible text and element info around the schedule area
        html = page.content()
        # Look for date/time picker related HTML
        import re

        # Find relevant sections
        for keyword in [
            "date-picker",
            "datepicker",
            "time-picker",
            "timepicker",
            "schedule",
            "When to post",
        ]:
            matches = list(
                re.finditer(
                    rf".{{0,200}}{re.escape(keyword)}.{{0,200}}", html, re.IGNORECASE
                )
            )
            if matches:
                print(f"\n--- Matches for '{keyword}' ---")
                for m in matches[:3]:
                    snippet = m.group(0).replace("\n", " ")
                    print(snippet[:400])

        # Also dump all input/div elements near schedule
        print("\n--- All visible elements after Schedule click ---")
        elements = page.locator(
            "xpath=//div[contains(@class,'schedule') or contains(@class,'date') or contains(@class,'time') or contains(@class,'picker') or contains(@class,'calendar')]"
        ).all()
        for el in elements[:20]:
            try:
                cls = el.get_attribute("class") or ""
                txt = (
                    el.inner_text()[:80].replace("\n", " ")
                    if el.is_visible()
                    else "(hidden)"
                )
                print(f"  class={cls[:80]!r}  text={txt!r}")
            except Exception:
                pass

        input("Press Enter to close browser…")
        browser.close()


if __name__ == "__main__":
    main()
