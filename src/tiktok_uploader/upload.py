"""
`tiktok_uploader` module for uploading videos to TikTok

Key Classes
-----------
TikTokUploader : Client for uploading videos to TikTok
"""

import datetime
import logging
import time
from collections.abc import Callable
from os.path import abspath, exists
from typing import Any, Literal

import pytz
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tiktok_uploader import config
from tiktok_uploader.auth import AuthBackend
from tiktok_uploader.browsers import get_browser
from tiktok_uploader.types import Cookie, ProxyDict, VideoDict
from tiktok_uploader.utils import bold, green, red

logger = logging.getLogger(__name__)


class TikTokUploader:
    def __init__(
        self,
        username: str = "",
        password: str = "",
        cookies: str = "",
        cookies_list: list[Cookie] = [],
        cookies_str: str | None = None,
        sessionid: str | None = None,
        proxy: ProxyDict | None = None,
        browser: Literal["chrome", "safari", "chromium", "edge", "firefox"] = "chrome",
        headless: bool = False,
        *args,
        **kwargs,
    ):
        """
        Initializes the TikTok Uploader client.

        The browser is not started until the first upload is attempted (lazy initialization).
        """
        self.auth = AuthBackend(
            username=username,
            password=password,
            cookies=cookies,
            cookies_list=cookies_list,
            cookies_str=cookies_str,
            sessionid=sessionid,
        )
        self.proxy = proxy
        self.browser_name = browser
        self.headless = headless
        self.browser_args = args
        self.browser_kwargs = kwargs

        self._page: Page | None = None
        self._browser_context: Any = (
            None  # Stored implicitly via page.context if needed
        )

    @property
    def page(self) -> Page:
        if self._page is None:
            logger.debug(
                "Create a %s browser instance %s",
                self.browser_name,
                "in headless mode" if self.headless else "",
            )
            self._page = get_browser(
                self.browser_name,
                headless=self.headless,
                proxy=self.proxy,
                *self.browser_args,
                **self.browser_kwargs,
            )  # type: ignore[misc]
            self._page = self.auth.authenticate_agent(self._page)
        return self._page

    def upload_video(
        self,
        filename: str,
        description: str = "",
        schedule: datetime.datetime | None = None,
        product_id: str | None = None,
        cover: str | None = None,
        visibility: Literal["everyone", "friends", "only_you"] = "everyone",
        num_retries: int = 1,
        skip_split_window: bool = False,
        sound: str | None = None,
        *args,
        **kwargs,
    ) -> bool:
        """
        Uploads a single TikTok video.

        Returns True if successful, False otherwise.
        """
        video_dict: VideoDict = {"path": filename}
        if description:
            video_dict["description"] = description
        if schedule:
            video_dict["schedule"] = schedule
        if product_id:
            video_dict["product_id"] = product_id
        if visibility != "everyone":
            video_dict["visibility"] = visibility
        if cover:
            video_dict["cover"] = cover
        if sound:
            video_dict["sound"] = sound

        failed_list = self.upload_videos(
            [video_dict], num_retries, skip_split_window, *args, **kwargs
        )

        return len(failed_list) == 0

    def upload_videos(
        self,
        videos: list[VideoDict],
        num_retries: int = 1,
        skip_split_window: bool = False,
        on_complete: Callable[[VideoDict], None] | None = None,
        *args,
        **kwargs,
    ) -> list[VideoDict]:
        """
        Uploads multiple videos to TikTok.
        Returns a list of failed videos.
        """
        videos = _convert_videos_dict(videos)  # type: ignore

        if videos and len(videos) > 1:
            logger.debug("Uploading %d videos", len(videos))

        page = self.page  # Triggers lazy loading/authentication

        failed = []
        # uploads each video
        for video in videos:
            try:
                path = abspath(video.get("path", "."))
                description = video.get("description", "")
                schedule = video.get("schedule", None)
                product_id = video.get("product_id", None)
                cover_path = video.get("cover", None)
                sound = video.get("sound", None)
                if cover_path is not None:
                    cover_path = abspath(cover_path)

                visibility = video.get("visibility", "everyone")

                logger.debug(
                    "Posting %s%s",
                    bold(video.get("path", "")),
                    (
                        f"\n{' ' * 15}with description: {bold(description)}"
                        if description
                        else ""
                    ),
                )

                # Video must be of supported type
                if not _check_valid_path(path):
                    print(f"{path} is invalid, skipping")
                    failed.append(video)
                    continue

                # Video must have a valid datetime for tiktok's scheduler
                if schedule:
                    timezone = pytz.UTC
                    if schedule.tzinfo is None:
                        schedule = schedule.astimezone(timezone)
                    elif (utc_offset := schedule.utcoffset()) is not None and int(
                        utc_offset.total_seconds()
                    ) == 0:  # Equivalent to UTC
                        schedule = timezone.localize(schedule)
                    else:
                        print(
                            f"{schedule} is invalid, the schedule datetime must be naive or aware with UTC timezone, skipping"
                        )
                        failed.append(video)
                        continue

                    valid_tiktok_minute_multiple = 5
                    schedule = _get_valid_schedule_minute(
                        schedule, valid_tiktok_minute_multiple
                    )
                    if not _check_valid_schedule(schedule):
                        print(
                            f"{schedule} is invalid, the schedule datetime must be as least 20 minutes in the future, and a maximum of 10 days, skipping"
                        )
                        failed.append(video)
                        continue

                complete_upload_form(
                    page,
                    path,
                    description,
                    schedule,
                    skip_split_window,
                    cover_path,
                    product_id,
                    visibility,
                    num_retries,
                    self.headless,
                    sound=sound,
                    *args,
                    **kwargs,
                )  # type: ignore[misc]
            except Exception as exception:
                logger.error("Failed to upload %s", path)
                logger.error(exception)
                failed.append(video)
                # import traceback
                # traceback.print_exc()

            if on_complete and callable(
                on_complete
            ):  # calls the user-specified on-complete function
                on_complete(video)

        return failed

    def close(self):
        """Closes the browser instance."""
        if self._page:
            try:
                self._page.context.browser.close()
            except Exception as e:
                logger.debug(f"Error closing browser: {e}")
            self._page = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Wrapper functions for backward compatibility (optional but good for transition)
def upload_video(
    filename: str,
    description: str | None = None,
    cookies: str = "",
    schedule: datetime.datetime | None = None,
    username: str = "",
    password: str = "",
    sessionid: str | None = None,
    cookies_list: list[Cookie] = [],
    cookies_str: str | None = None,
    proxy: ProxyDict | None = None,
    product_id: str | None = None,
    cover: str | None = None,
    visibility: Literal["everyone", "friends", "only_you"] = "everyone",
    browser: Literal["chrome", "safari", "chromium", "edge", "firefox"] = "chrome",
    headless: bool = False,
    *args,
    **kwargs,
) -> list[VideoDict]:
    """
    Uploads a single TikTok video using the TikTokUploader class.

    Returns a list of failed videos (empty if successful).
    """
    uploader = TikTokUploader(
        username=username,
        password=password,
        cookies=cookies,
        cookies_list=cookies_list,
        cookies_str=cookies_str,
        sessionid=sessionid,
        proxy=proxy,
        browser=browser,
        headless=headless,
        *args,
        **kwargs,
    )  # type: ignore[misc]

    video_dict: VideoDict = {"path": filename}
    if description:
        video_dict["description"] = description
    if schedule:
        video_dict["schedule"] = schedule
    if product_id:
        video_dict["product_id"] = product_id
    if visibility != "everyone":
        video_dict["visibility"] = visibility
    if cover:
        video_dict["cover"] = cover

    try:
        return uploader.upload_videos([video_dict], *args, **kwargs)
    finally:
        if config.quit_on_end:
            uploader.close()


def upload_videos(
    videos: list[VideoDict],
    username: str = "",
    password: str = "",
    cookies: str = "",
    cookies_list: list[Cookie] = [],
    cookies_str: str | None = None,
    sessionid: str | None = None,
    proxy: ProxyDict | None = None,
    browser: Literal["chrome", "safari", "chromium", "edge", "firefox"] = "chrome",
    browser_agent: (
        Page | None
    ) = None,  # Not fully supported in new class-based approach as constructor
    headless: bool = False,
    *args,
    **kwargs,
) -> list[VideoDict]:
    """
    Uploads multiple videos to TikTok using the TikTokUploader class.
    """
    uploader = TikTokUploader(
        username=username,
        password=password,
        cookies=cookies,
        cookies_list=cookies_list,
        cookies_str=cookies_str,
        sessionid=sessionid,
        proxy=proxy,
        browser=browser,
        headless=headless,
        *args,
        **kwargs,
    )  # type: ignore[misc]

    if browser_agent:
        uploader._page = uploader.auth.authenticate_agent(browser_agent)

    try:
        return uploader.upload_videos(videos, *args, **kwargs)
    finally:
        if config.quit_on_end:
            uploader.close()


def complete_upload_form(
    page: Page,
    path: str,
    description: str,
    schedule: datetime.datetime | None,
    skip_split_window: bool,
    cover_path: str | None = None,
    product_id: str | None = None,
    visibility: Literal["everyone", "friends", "only_you"] = "everyone",
    num_retries: int = 1,
    headless: bool = False,
    sound: str | None = None,
    *args,
    **kwargs,
) -> None:
    """
    Actually uploads each video
    """
    _go_to_upload(page)
    _remove_cookies_window(page)
    _dismiss_feature_popup(page)

    _set_video(page, path=path, num_retries=num_retries, **kwargs)
    _dismiss_feature_popup(page)  # may appear again after video processes

    if sound:
        _set_sound(page, sound)
    if cover_path:
        _set_cover(page, cover_path)
    if not skip_split_window:
        _remove_split_window(page)
    _set_interactivity(page, **kwargs)
    _set_description(page, description)
    if visibility != "everyone":
        _set_visibility(page, visibility)
    if schedule:
        _set_schedule_video(page, schedule)
    if product_id:
        _add_product_link(page, product_id)
    _post_video(page)


def _go_to_upload(page: Page) -> None:
    """
    Navigates to the upload page
    """
    logger.debug(green("Navigating to upload page"))

    if page.url != config.paths.upload:
        page.goto(str(config.paths.upload))
    else:
        # refresh
        page.reload()
        # TODO: handle alert if any (Playwright auto-dismisses dialogs usually, or we can handle)
        page.on("dialog", lambda dialog: dialog.accept())

    # waits for the root to load
    page.wait_for_selector("#root", timeout=config.explicit_wait * 1000)


def _set_description(page: Page, description: str) -> None:
    """
    Sets the description of the video
    """
    if description is None:
        return

    logger.debug(green("Setting description"))

    # Remove any characters outside the BMP range (emojis, etc) & Fix accents
    description = description.encode("utf-8", "ignore").decode("utf-8")
    saved_description = description

    try:
        desc_locator = page.locator(f"xpath={config.selectors.upload.description}")
        desc_locator.wait_for(state="visible", timeout=config.implicit_wait * 1000)

        desc_locator.click()

        # Select all existing text and delete it.
        # On macOS, Ctrl+A moves cursor to line start; Meta+A (Cmd+A) selects all.
        desc_locator.press("Meta+A")
        desc_locator.press("Backspace")
        # Belt-and-suspenders: repeat with Ctrl+A for Linux/Windows compatibility
        desc_locator.press("Control+A")
        desc_locator.press("Backspace")

        desc_locator.click()
        time.sleep(1)

        words = description.split(" ")
        for word in words:
            if word[0] == "#":
                desc_locator.press_sequentially(word, delay=50)
                time.sleep(0.5)

                mention_box = page.locator(
                    f"xpath={config.selectors.upload.mention_box}"
                )
                try:
                    mention_box.wait_for(
                        state="visible", timeout=config.add_hashtag_wait * 1000
                    )
                    desc_locator.press("Enter")
                except Exception:
                    pass

            elif word[0] == "@":
                logger.debug(green("- Adding Mention: " + word))
                desc_locator.press_sequentially(word)
                time.sleep(1)

                mention_box_user_id = page.locator(
                    f"xpath={config.selectors.upload.mention_box_user_id}"
                )
                try:
                    mention_box_user_id.first.wait_for(state="visible", timeout=5000)

                    found = False
                    user_ids = mention_box_user_id.all()

                    target_username = word[1:].lower()

                    for i, user_el in enumerate(user_ids):
                        if user_el.is_visible():
                            text = user_el.inner_text().split(" ")[0]
                            if text.lower() == target_username:
                                found = True
                                print("Matching User found : Clicking User")
                                for _ in range(i):
                                    desc_locator.press("ArrowDown")
                                desc_locator.press("Enter")
                                break

                    if not found:
                        desc_locator.press_sequentially(" ")

                except Exception:
                    desc_locator.press_sequentially(" ")

            else:
                desc_locator.press_sequentially(word + " ")

    except Exception as exception:
        print("Failed to set description: ", exception)
        # fallback
        _clear(desc_locator)
        desc_locator.fill(saved_description)


def _clear(locator) -> None:
    """
    Clears the text of the element
    """
    locator.press("Control+A")
    locator.press("Backspace")


def _set_video(page: Page, path: str = "", num_retries: int = 3, **kwargs) -> None:
    """
    Sets the video to upload
    """
    logger.debug(green("Uploading video file"))

    for _ in range(num_retries):
        try:
            upload_box = page.locator(f"xpath={config.selectors.upload.upload_video}")
            upload_box.set_input_files(path)

            # wait until a non-draggable image is found (process confirmation)
            process_confirmation = page.locator(
                f"xpath={config.selectors.upload.process_confirmation}"
            )
            process_confirmation.wait_for(
                state="attached", timeout=config.explicit_wait * 1000
            )
            return
        except PlaywrightTimeoutError as exception:
            print("TimeoutException occurred:\n", exception)
        except Exception as exception:
            print(exception)
            raise FailedToUpload(exception)


def _dismiss_feature_popup(page: Page) -> None:
    """
    Dismisses TikTok's 'New editing features added' (or similar) modal
    by clicking any 'Got it' / 'OK' / 'Close' button that appears.
    """
    try:
        # Match button by visible text — covers 'Got it', 'Got It', etc.
        btn = page.locator(
            "//button[contains(translate(., 'GOTIQUK', 'gotiquk'), 'got it')]"
            " | //button[contains(translate(., 'GOTIQUK', 'gotiquk'), 'ok')]"
            " | //div[contains(@class,'modal')]//button"
        ).first
        if btn.is_visible(timeout=4000):
            btn.click()
            logger.debug(green("Dismissed feature popup"))
    except Exception:
        pass


def _set_sound(page: Page, sound: str) -> None:
    """
    Adds a sound to the video via the TikTok Studio editor panel.
    Flow:
      1. Click 'Sounds' button in the editor toolbar
      2. Search for the song
      3. Click '+' on the first result → song added to timeline
      4. Mute the song audio track (second row in timeline)
      5. Click 'Save'
    Non-fatal — logs a warning on any failure.
    """
    logger.debug(green(f"Adding sound: {sound}"))
    try:
        # ── Step 1: Click 'Sounds' in the editor toolbar ──────────────────
        sounds_btn = None
        for sel in [
            "xpath=//button[normalize-space()='Sounds']",
            "xpath=//button[.//span[normalize-space()='Sounds']]",
            "xpath=//div[@role='button' and normalize-space()='Sounds']",
            "xpath=//*[normalize-space()='Sounds' and (self::button or @role='button')]",
            "xpath=//span[normalize-space()='Sounds']/ancestor::button[1]",
            "xpath=//span[normalize-space()='Sounds']/ancestor::div[@role='button'][1]",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    sounds_btn = el
                    break
            except Exception:
                continue

        if sounds_btn is None:
            logger.warning("Sounds button not found — skipping sound")
            return

        sounds_btn.click()
        time.sleep(1.5)

        # ── Step 2: Search for the song ────────────────────────────────────
        search_box = None
        for sel in [
            "xpath=//input[contains(@placeholder,'Search sounds') or contains(@placeholder,'search sounds')]",
            "xpath=//input[contains(@placeholder,'Search') and ancestor::*[contains(@class,'sound') or contains(@class,'Sound')]]",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=6000):
                    search_box = el
                    break
            except Exception:
                continue

        if search_box is None:
            logger.warning("Sound search box not found — skipping sound")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return

        search_box.click()
        search_box.fill(sound)
        time.sleep(0.5)
        page.keyboard.press("Enter")
        time.sleep(3)  # wait for results to load

        # ── Step 3: Click '+' on the first search result ───────────────────
        # Wait a bit longer to ensure results have fully rendered
        time.sleep(2)

        add_btn = None
        for sel in [
            # Explicit aria-label Add button
            "xpath=(//button[@aria-label='Add' or @aria-label='add'])[1]",
            # Button containing only a '+' character
            "xpath=(//button[normalize-space(.)='+'])[1]",
            # Button with title 'Add'
            "xpath=(//button[@title='Add' or @title='add'])[1]",
            # Any list item's last button (the + circle at right of row)
            "xpath=(//li[.//button])[1]//button[last()]",
            # Any div-row's last button
            "xpath=(//div[@role='listitem' or @role='option'][.//button])[1]//button[last()]",
            # SVG button that is NOT the search/clear/close/back button
            "xpath=(//button[.//svg][not(contains(@class,'close'))][not(contains(@class,'clear'))][not(contains(@class,'back'))][not(contains(@class,'search'))])[last()]",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    add_btn = el
                    break
            except Exception:
                continue

        if add_btn is None:
            logger.warning(f"No sound results found for '{sound}' — skipping")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return

        add_btn.click()
        time.sleep(1.5)

        # ── Step 4: Close the Sounds panel ────────────────────────────────
        # Clicking + does NOT always close the panel. Close it explicitly so
        # the panel buttons don't interfere with the speaker button search.
        try:
            # The panel header has an × button; try several selectors.
            closed = False
            for close_sel in [
                "xpath=//div[contains(@class,'SoundPanel') or contains(@class,'Sounds')]//button[@aria-label='Close' or @aria-label='close' or @title='Close']",
                "xpath=//div[contains(text(),'Sounds')]/following-sibling::button",
                "xpath=//div[@class[contains(.,'sound') or contains(.,'Sound')]]//button[.//svg][1]",
            ]:
                try:
                    el = page.locator(close_sel).first
                    if el.is_visible(timeout=1000):
                        el.click()
                        closed = True
                        break
                except Exception:
                    continue
            if not closed:
                page.keyboard.press("Escape")
            time.sleep(1)
        except Exception:
            pass

        # ── Step 5: Click song track → set volume to -60 dB (silent) ─────────
        try:
            # Click the audio track bar to select it and open the Audio panel.
            sound_word = (sound or "").split()[0]
            selected = False
            for sel in [
                f"xpath=(//div[contains(@class,'track') or contains(@class,'Track') or contains(@class,'audio') or contains(@class,'Audio')][.//*[contains(text(),'{sound_word}')]])[1]",
                f"xpath=(//*[contains(text(),'{sound_word}') and not(ancestor::*[contains(@class,'SoundPanel') or contains(@class,'search') or contains(@class,'list')])])[last()]",
            ]:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=1500):
                        el.dispatch_event("click")
                        selected = True
                        break
                except Exception:
                    continue
            if not selected:
                vp = page.viewport_size or {"width": 1280, "height": 720}
                page.mouse.click(int(vp["width"] * 0.5), int(vp["height"] * 0.91))
            time.sleep(1.5)

            # The Volume input has class PropSettingInput__input; the topmost
            # visible one (lowest y) is Volume, the others are Fade in/out.
            vol_input = page.locator("input.PropSettingInput__input").first
            vol_input.wait_for(state="visible", timeout=5000)
            vol_input.click()
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.type("-60")
            page.keyboard.press("Enter")
            time.sleep(0.4)
            logger.debug(green("Song track volume set to -60 dB (silent)"))
        except Exception as mute_exc:
            logger.warning(f"Could not silence song track: {mute_exc}")

        # ── Step 5: Click Save ─────────────────────────────────────────────
        # Save exits the editor and returns to the main upload form.
        save_btn = None
        for sel in [
            "xpath=//button[normalize-space()='Save']",
            "xpath=//button[.//span[normalize-space()='Save']]",
        ]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=5000):
                    save_btn = el
                    break
            except Exception:
                continue

        if save_btn is None:
            logger.warning("Save button not found — sound added but not saved")
            return

        save_btn.click()
        time.sleep(2)

        logger.debug(green(f"Sound set and saved: {sound}"))
    except Exception as exc:
        logger.warning(
            f"Failed to add sound '{sound}': {exc} — continuing without sound"
        )


def _remove_cookies_window(page: Page) -> None:
    """
    Removes the cookies window if it is open
    """
    logger.debug(green("Removing cookies window"))

    try:
        selector = f"{config.selectors.upload.cookies_banner.banner} >> {config.selectors.upload.cookies_banner.button} >> button"

        button = page.locator(selector).first
        if button.is_visible(timeout=5000):
            button.click()

    except Exception:
        page.evaluate(
            f"""
            const banner = document.querySelector("{config.selectors.upload.cookies_banner.banner}");
            if (banner) banner.remove();
        """
        )


def _remove_split_window(page: Page) -> None:
    """
    Remove the split window if it is open
    """
    logger.debug(green("Removing split window"))
    window_xpath = config.selectors.upload.split_window

    try:
        window = page.locator(f"xpath={window_xpath}")
        if window.is_visible(timeout=config.implicit_wait * 1000):
            window.click()
    except PlaywrightTimeoutError:
        logger.debug(red("Split window not found or operation timed out"))


def _set_interactivity(
    page: Page,
    comment: bool = True,
    stitch: bool = True,
    duet: bool = True,
    *args,
    **kwargs,
) -> None:
    """
    Sets the interactivity settings of the video
    """
    try:
        logger.debug(green("Setting interactivity settings"))

        comment_box = page.locator(f"xpath={config.selectors.upload.comment}")
        stitch_box = page.locator(f"xpath={config.selectors.upload.stitch}")
        duet_box = page.locator(f"xpath={config.selectors.upload.duet}")

        if comment ^ comment_box.is_checked():
            comment_box.click()

        if stitch ^ stitch_box.is_checked():
            stitch_box.click()

        if duet ^ duet_box.is_checked():
            duet_box.click()

    except Exception as _:
        logger.error("Failed to set interactivity settings")


def _set_visibility(
    page: Page, visibility: Literal["everyone", "friends", "only_you"]
) -> None:
    """
    Sets the visibility/privacy of the video
    """
    try:
        logger.debug(green(f"Setting visibility to: {visibility}"))

        dropdown_xpath = (
            "//div[@data-e2e='video_visibility_container']//button[@role='combobox']"
        )
        dropdown = page.locator(f"xpath={dropdown_xpath}")
        dropdown.click()
        time.sleep(1.5)

        visibility_text_map = {
            "everyone": "Everyone",
            "friends": "Friends",
            "only_you": "Only you",
        }

        option_text = visibility_text_map.get(visibility, "Everyone")
        option_xpath = f"//div[@role='option' and contains(., '{option_text}')]"

        option = page.locator(f"xpath={option_xpath}")
        option.scroll_into_view_if_needed()
        time.sleep(0.5)
        option.click()

        logger.debug(green(f"Successfully set visibility to: {visibility}"))

    except Exception as e:
        logger.error(red(f"Failed to set visibility: {e}"))


def _set_schedule_video(page: Page, schedule: datetime.datetime) -> None:
    """
    Sets the schedule of the video
    """
    logger.debug(green("Setting schedule"))

    timezone_str = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    driver_timezone = pytz.timezone(timezone_str)

    schedule = schedule.astimezone(driver_timezone)

    month = schedule.month
    day = schedule.day
    hour = schedule.hour
    minute = schedule.minute

    try:
        # TikTok shows a "When to post" section with "Now" and "Schedule" radio buttons.
        # The radio <input> is visually hidden; we must click the visible label/span.
        clicked = False
        label_selectors = [
            "label:has-text('Schedule')",
            "xpath=//label[contains(normalize-space(.), 'Schedule')]",
            "xpath=//span[normalize-space(text())='Schedule']",
            "xpath=//div[normalize-space(text())='Schedule']",
        ]
        for sel in label_selectors:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    clicked = True
                    logger.debug(green("Clicked Schedule label"))
                    break
            except Exception:
                continue

        if not clicked:
            # Fallback: force-click the hidden radio input
            for sel in [
                "xpath=//input[@type='radio'][2]",
                "xpath=//label[contains(.,'Schedule')]/input",
                "xpath=//*[@id='tux-1']",
            ]:
                try:
                    page.locator(sel).first.click(force=True)
                    clicked = True
                    logger.debug(green("Force-clicked Schedule radio input"))
                    break
                except Exception:
                    continue

        if not clicked:
            raise Exception("Could not find or click Schedule toggle")

        time.sleep(1)  # wait for date/time pickers OR low-quality popup to appear

        # TikTok sometimes shows a "low quality" warning when Schedule is clicked.
        # Close it with X (do NOT click "Replace Video"), then re-click Schedule.
        low_quality_dismissed = False
        try:
            close_btn = page.locator(
                "xpath=//div[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'popup')]"
                "//button[@aria-label='Close' or contains(@class,'close') or contains(@class,'dismiss')]"
                " | xpath=//*[@data-e2e='close-btn']"
                " | xpath=//button[contains(@aria-label,'lose')]"
            ).first
            if close_btn.is_visible(timeout=2000):
                close_btn.click()
                low_quality_dismissed = True
                logger.debug(green("Dismissed low-quality warning popup"))
                time.sleep(0.5)
        except Exception:
            pass

        # If we dismissed a popup, the Schedule radio may have been de-selected — re-click it.
        if low_quality_dismissed:
            for sel in label_selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        el.click()
                        logger.debug(
                            green("Re-clicked Schedule label after popup dismissal")
                        )
                        time.sleep(1)
                        break
                except Exception:
                    continue

        __date_picker(page, month, day)
        __time_picker(page, hour, minute)
    except Exception as e:
        msg = f"Failed to set schedule: {e}"
        logger.error(red(msg))
        raise FailedToUpload()


def __date_picker(page: Page, month: int, day: int) -> None:
    logger.debug(green("Picking date"))

    # The date input is inside div.scheduled-picker and has a value like "2026-02-26"
    date_input = page.locator(
        "xpath=//div[contains(@class,'scheduled-picker')]//input[contains(@value,'-')]"
    ).first
    date_input.wait_for(state="visible", timeout=5000)
    date_input.click()
    time.sleep(0.5)

    # Wait for the calendar to be injected into the DOM
    calendar = None
    for sel in [
        "xpath=//div[contains(@class,'calendar-wrapper')]",
        "xpath=//div[contains(@class,'calendar')]",
    ]:
        try:
            el = page.locator(sel).first
            el.wait_for(state="visible", timeout=5000)
            calendar = el
            break
        except Exception:
            continue

    if calendar is None:
        raise Exception("Calendar not found after clicking date input")

    # Read current month from calendar header (e.g. "February / 2026" or "February")
    n_calendar_month = month  # fallback: assume already correct
    for sel in [
        "xpath=//span[contains(@class,'month-title')]",
        "xpath=//div[contains(@class,'month-title')]",
    ]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                text = el.inner_text().strip()
                month_part = text.split("/")[0].split()[0].strip()
                n_calendar_month = datetime.datetime.strptime(month_part, "%B").month
                break
        except Exception:
            continue

    if n_calendar_month != month:
        arrows = page.locator(f"xpath={config.selectors.schedule.calendar_arrows}")
        if n_calendar_month < month:
            arrows.last.click()
        else:
            arrows.first.click()
        time.sleep(0.5)

    # Click the matching day cell
    day_selectors = [
        "xpath=//div[contains(@class,'days-wrapper')]//span[contains(@class,'day') and contains(@class,'valid')]",
        "xpath=//span[contains(@class,'day') and contains(@class,'valid')]",
        "xpath=//div[contains(@class,'days-wrapper')]//span[contains(@class,'day')]",
    ]
    valid_days = []
    for sel in day_selectors:
        try:
            candidates = page.locator(sel).all()
            if candidates:
                valid_days = candidates
                break
        except Exception:
            continue

    day_to_click = None
    for day_option in valid_days:
        try:
            if int(day_option.inner_text().strip()) == day:
                day_to_click = day_option
                break
        except Exception:
            continue

    if day_to_click:
        day_to_click.click()
    else:
        raise Exception(f"Day {day} not found in calendar")

    __verify_date_picked_is_correct(page, month, day)


def __verify_date_picked_is_correct(page: Page, month: int, day: int) -> None:
    # Read back the date input value (format: YYYY-MM-DD)
    date_input = page.locator(
        "xpath=//div[contains(@class,'scheduled-picker')]//input[contains(@value,'-')]"
    ).first
    date_selected = date_input.get_attribute("value") or ""
    try:
        date_selected_month = int(date_selected.split("-")[1])
        date_selected_day = int(date_selected.split("-")[2])
    except Exception:
        logger.debug(green(f"Could not verify date — raw value: {date_selected!r}"))
        return

    if date_selected_month == month and date_selected_day == day:
        logger.debug(green("Date picked correctly"))
    else:
        msg = f"Date picker mismatch: expected {month}-{day} but got {date_selected_month}-{date_selected_day}"
        logger.error(msg)
        raise Exception(msg)


def __time_picker(page: Page, hour: int, minute: int) -> None:
    logger.debug(green("Picking time"))

    # The time input is inside div.scheduled-picker — value like "22:30" (no dash)
    time_picker = page.locator(
        "xpath=//div[contains(@class,'scheduled-picker')]//input[not(contains(@value,'-'))]"
    ).first
    if not time_picker.is_visible(timeout=5000):
        raise Exception("Time picker input not found")

    time_picker.click()
    time.sleep(0.5)

    # Wait for the drum-roll container to appear
    container_sel = (
        "xpath=//div[contains(@class,'tiktok-timepicker-time-picker-container')"
        " and not(contains(@class,'invisible'))]"
    )
    page.locator(container_sel).first.wait_for(state="visible", timeout=5000)

    # --- Hour: use direct XPath text match (handles both "9" and "09") ---
    hour_xpath = (
        f"xpath=//span[contains(@class,'tiktok-timepicker-left')"
        f" and (normalize-space(text())='{hour}'"
        f" or normalize-space(text())='{hour:02d}')]"
    )
    hour_el = page.locator(hour_xpath).first
    hour_el.scroll_into_view_if_needed()
    time.sleep(0.5)
    hour_el.click()
    time.sleep(0.3)

    # --- Minute: use direct XPath text match (always zero-padded: "00", "05" …) ---
    minute_xpath = (
        f"xpath=//span[contains(@class,'tiktok-timepicker-right')"
        f" and (normalize-space(text())='{minute:02d}'"
        f" or normalize-space(text())='{minute}')]"
    )
    minute_el = page.locator(minute_xpath).first
    minute_el.scroll_into_view_if_needed()
    time.sleep(0.5)
    minute_el.click()
    time.sleep(0.3)

    # Close the time picker
    time_picker.click()
    time.sleep(0.5)

    __verify_time_picked_is_correct(page, hour, minute)


def __verify_time_picked_is_correct(page: Page, hour: int, minute: int) -> None:
    # Read back the time input value attribute (format: "HH:MM")
    time_input = page.locator(
        "xpath=//div[contains(@class,'scheduled-picker')]//input[not(contains(@value,'-'))]"
    ).first
    time_selected = time_input.get_attribute("value") or ""
    try:
        time_selected_hour = int(time_selected.split(":")[0])
        time_selected_minute = int(time_selected.split(":")[1])
    except Exception:
        logger.debug(green(f"Could not verify time — raw value: {time_selected!r}"))
        return

    if time_selected_hour == hour and time_selected_minute == minute:
        logger.debug(green("Time picked correctly"))
    else:
        msg = (
            f"Time picker mismatch: expected {hour:02d}:{minute:02d} "
            f"but got {time_selected_hour:02d}:{time_selected_minute:02d}"
        )
        raise Exception(msg)


def _post_video(page: Page) -> None:
    """
    Posts the video
    """
    logger.debug(green("Clicking the post button"))

    post_btn = page.locator(f"xpath={config.selectors.upload.post}")
    try:

        def is_enabled():
            return post_btn.get_attribute("data-disabled") == "false"

        for _ in range(int(config.uploading_wait / 2)):
            if is_enabled():
                break
            time.sleep(2)

        post_btn.scroll_into_view_if_needed()
        post_btn.click()

    except Exception:
        logger.debug(green("Trying to click on the button again (fallback)"))
        page.evaluate('document.querySelector(".TUXButton--primary").click()')

    try:
        post_now = page.locator(f"xpath={config.selectors.upload.post_now}")
        if post_now.is_visible(timeout=5000):
            post_now.click()
    except Exception:
        pass


def _post_video(page: Page) -> None:
    """
    Posts the video
    """
    logger.debug(green("Clicking the post button"))

    post_btn = page.locator(f"xpath={config.selectors.upload.post}")
    try:

        def is_enabled():
            return post_btn.get_attribute("data-disabled") == "false"

        for _ in range(int(config.uploading_wait / 2)):
            if is_enabled():
                break
            time.sleep(2)

        post_btn.scroll_into_view_if_needed()
        post_btn.click()

    except Exception:
        logger.debug(green("Trying to click on the button again (fallback)"))
        page.evaluate('document.querySelector(".TUXButton--primary").click()')

    try:
        post_now = page.locator(f"xpath={config.selectors.upload.post_now}")
        if post_now.is_visible(timeout=5000):
            post_now.click()
    except Exception:
        pass

    # Poll for success confirmation OR low-quality warning popup.
    # If the popup appears, dismiss it with X and re-click Post.
    post_confirmation = page.locator(
        f"xpath={config.selectors.upload.post_confirmation}"
    )
    low_quality_close = page.locator(
        "xpath=//div[contains(@class,'modal') or contains(@class,'dialog') or contains(@class,'popup')]"
        "//button[@aria-label='Close' or contains(@class,'close') or contains(@class,'dismiss')]"
        " | xpath=//*[@data-e2e='close-btn']"
        " | xpath=//button[contains(@aria-label,'lose')]"
    )

    deadline = time.time() + config.explicit_wait
    while time.time() < deadline:
        # Success?
        try:
            if post_confirmation.is_visible(timeout=1000):
                logger.debug(green("Video posted successfully"))
                return
        except Exception:
            pass

        # Low-quality popup blocking the post?
        try:
            if low_quality_close.first.is_visible(timeout=1000):
                low_quality_close.first.click()
                logger.debug(
                    green(
                        "Dismissed low-quality popup after Post click — retrying Post"
                    )
                )
                time.sleep(0.5)
                try:
                    post_btn.click()
                except Exception:
                    try:
                        page.evaluate(
                            'document.querySelector(".TUXButton--primary").click()'
                        )
                    except Exception:
                        pass
        except Exception:
            pass

        time.sleep(1)

    # Final check
    post_confirmation.wait_for(state="attached", timeout=5000)
    logger.debug(green("Video posted successfully"))


def _add_product_link(page: Page, product_id: str) -> None:
    """
    Adds the product link
    """
    logger.debug(green(f"Attempting to add product link for ID: {product_id}..."))
    try:
        add_link_button = page.locator(
            "//button[contains(@class, 'Button__root') and contains(., 'Add')]"
        )
        add_link_button.click()
        time.sleep(1)

        try:
            first_next = page.locator(
                "//button[contains(@class, 'TUXButton--primary') and .//div[text()='Next']]"
            )
            if first_next.is_visible(timeout=3000):
                first_next.click()
                time.sleep(1)
        except Exception:
            pass

        search_input = page.locator("//input[@placeholder='Search products']")
        search_input.fill(product_id)
        search_input.press("Enter")
        time.sleep(3)

        product_radio = page.locator(
            f"//tr[.//span[contains(text(), '{product_id}')] or .//div[contains(text(), '{product_id}')]]//input[@type='radio' and contains(@class, 'TUXRadioStandalone-input')]"
        )
        product_radio.click()
        time.sleep(1)

        second_next = page.locator(
            "//button[contains(@class, 'TUXButton--primary') and .//div[text()='Next']]"
        )
        second_next.click()
        time.sleep(1)

        final_add = page.locator(
            "//button[contains(@class, 'TUXButton--primary') and .//div[text()='Add']]"
        )
        final_add.click()

        final_add.wait_for(state="hidden")

    except Exception as e:
        logger.error(red(f"Error adding product link: {e}"))


def _set_cover(page: Page, cover_path: str) -> None:
    """
    Adds a custom cover
    """
    logger.debug(green(f"Attempting to add custom cover: {cover_path}..."))
    try:
        if not _check_valid_cover_path(cover_path):
            raise Exception("Invalid cover image file path")

        preview_loc = page.locator(
            f"xpath={config.selectors.upload.cover.cover_preview}"
        )
        current_cover_src = preview_loc.get_attribute("src")

        edit_cover_btn = page.locator(
            f"xpath={config.selectors.upload.cover.edit_cover_button}"
        )
        edit_cover_btn.click()

        upload_tab = page.locator(
            f"xpath={config.selectors.upload.cover.upload_cover_tab}"
        )
        upload_tab.click()

        upload_box = page.locator(f"xpath={config.selectors.upload.cover.upload_cover}")
        upload_box.set_input_files(cover_path)

        confirm_btn = page.locator(
            f"xpath={config.selectors.upload.cover.upload_confirmation}"
        )
        confirm_btn.click()

        def check_src_change():
            return preview_loc.get_attribute("src") != current_cover_src

        for _ in range(20):
            if check_src_change():
                break
            time.sleep(0.5)

    except Exception as e:
        logger.error(red(f"Error setting cover: {e}"))
        try:
            exit_icon = page.locator(
                f"xpath={config.selectors.upload.cover.exit_cover_container}"
            )
            if exit_icon.is_visible():
                exit_icon.click()
        except Exception:
            pass


def _check_valid_path(path: str) -> bool:
    return exists(path) and path.split(".")[-1] in config.supported_file_types


def _check_valid_cover_path(path: str) -> bool:
    return exists(path) and path.split(".")[-1] in config.supported_image_file_types


def _get_valid_schedule_minute(
    schedule: datetime.datetime, valid_multiple
) -> datetime.datetime:
    if _is_valid_schedule_minute(schedule.minute, valid_multiple):
        return schedule
    else:
        return _set_valid_schedule_minute(schedule, valid_multiple)


def _is_valid_schedule_minute(minute: int, valid_multiple) -> bool:
    if minute % valid_multiple != 0:
        return False
    else:
        return True


def _set_valid_schedule_minute(
    schedule: datetime.datetime, valid_multiple: int
) -> datetime.datetime:
    minute = schedule.minute
    remainder = minute % valid_multiple
    integers_to_valid_multiple = 5 - remainder
    schedule += datetime.timedelta(minutes=integers_to_valid_multiple)
    return schedule


def _check_valid_schedule(schedule: datetime.datetime) -> bool:
    valid_tiktok_minute_multiple = 5
    margin_to_complete_upload_form = 5
    datetime_utc_now = pytz.UTC.localize(datetime.datetime.utcnow())
    min_datetime_tiktok_valid = datetime_utc_now + datetime.timedelta(minutes=15)
    min_datetime_tiktok_valid += datetime.timedelta(
        minutes=margin_to_complete_upload_form
    )
    max_datetime_tiktok_valid = datetime_utc_now + datetime.timedelta(days=10)
    if schedule < min_datetime_tiktok_valid or schedule > max_datetime_tiktok_valid:
        return False
    elif not _is_valid_schedule_minute(schedule.minute, valid_tiktok_minute_multiple):
        return False
    else:
        return True


def _convert_videos_dict(
    videos_list_of_dictionaries: list[dict[str, Any]],
) -> list[VideoDict]:
    if not videos_list_of_dictionaries:
        raise RuntimeError("No videos to upload")

    valid_path = config.valid_path_names
    valid_description = config.valid_descriptions

    correct_path = valid_path[0]
    correct_description = valid_description[0]

    def intersection(lst1, lst2):
        return list(set(lst1) & set(lst2))

    return_list: list[VideoDict] = []
    for elem in videos_list_of_dictionaries:
        elem = {k.strip().lower(): v for k, v in elem.items()}
        keys = elem.keys()
        path_intersection = intersection(valid_path, keys)
        description_intersection = intersection(valid_description, keys)

        if path_intersection:
            path = elem[path_intersection.pop()]
            if not _check_valid_path(path):
                raise RuntimeError("Invalid path: " + path)
            elem[correct_path] = path
        else:
            for _, value in elem.items():
                if _check_valid_path(value):
                    elem[correct_path] = value
                    break
            else:
                raise RuntimeError("Path not found in dictionary: " + str(elem))

        if description_intersection:
            elem[correct_description] = elem[description_intersection.pop()]
        else:
            for _, value in elem.items():
                if not _check_valid_path(value):
                    elem[correct_description] = value
                    break
            else:
                elem[correct_description] = ""

        return_list.append(elem)  # type: ignore

    return return_list


class DescriptionTooLong(Exception):
    def __init__(self, message: str | None = None):
        super().__init__(message or self.__doc__)


class FailedToUpload(Exception):
    def __init__(self, message=None):
        super().__init__(message or self.__doc__)
