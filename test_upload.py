#!/usr/bin/env python3
"""
Single-video upload test — uses the same 9AM-10PM slot logic as process_videos.py.
Usage:  uv run python test_upload.py videos/YOUR_VIDEO.mp4 [--headless] [--debug]
"""
import argparse
import asyncio
import logging
import re
from pathlib import Path


class _NoCookieFilter(logging.Filter):
    """Suppress noisy cookie debug lines from the uploader."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "cookie" not in msg.lower()


from process_videos import (
    recognize_song,
    build_description,
    next_upload_slots,
    load_state,
    save_state,
    COOKIES_FILE,
    _clean_text,
)
from tiktok_uploader.upload import TikTokUploader
from datetime import datetime


async def get_description(video: Path) -> tuple[str, str | None]:
    result = await recognize_song(video)
    sound: str | None = None
    if result and "track" in result:
        track = result["track"]
        title = _clean_text(track.get("title", "")).strip()
        artist = _clean_text(track.get("subtitle", "")).strip()
        print(f"  Song  : {title}")
        print(f"  Artist: {artist}")
        # Use only the first credited artist for the search query
        first_artist = re.split(r"\s*(?:&|,|ft\.|feat\.)\s*", artist, maxsplit=1)[
            0
        ].strip()
        sound = f"{title} {first_artist}".strip() or None
    else:
        print("  No song detected → using default tags")
    return build_description(result), sound


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test-upload a single video to TikTok."
    )
    parser.add_argument("video", nargs="?", help="Path to the .mp4 file to upload")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headlessly (default: visible)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show ALL debug output including cookie messages (default: upload messages only)",
    )
    args = parser.parse_args()

    # Always show upload progress (DEBUG), but filter out noisy cookie lines.
    # Pass --debug to see everything including cookie messages.
    _cookie_filter = _NoCookieFilter()
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
    if not args.debug:
        # tiktok_uploader no longer adds its own handler — all records propagate
        # to the root logger, so filtering root is sufficient.
        for handler in logging.root.handlers:
            handler.addFilter(_cookie_filter)
    logging.getLogger("tiktok_uploader").setLevel(logging.DEBUG)

    if args.video:
        video = Path(args.video)
        # If a directory was passed, pick the first .mp4 inside it
        if video.is_dir():
            candidates = sorted(video.glob("*.mp4"))
            if not candidates:
                print(f"No .mp4 files found in {video}")
                return
            video = candidates[0]
    else:
        videos = sorted(Path("videos").glob("*.mp4"))
        if not videos:
            print(
                "No video found. Pass a path: uv run python test_upload.py videos/X.mp4"
            )
            return
        video = videos[0]

    if not video.exists():
        print(f"File not found: {video}")
        return

    # Load state to check for duplicates and get last slot
    state = load_state()
    if video.name in state.get("uploaded", []):
        print(
            f"Already uploaded: {video.name} (in uploaded.json) — delete entry to re-upload"
        )
        return

    last_slot_iso = state.get("last_slot")
    last_slot = (
        datetime.fromisoformat(last_slot_iso).astimezone() if last_slot_iso else None
    )

    # Get next available slot in the 9AM–10PM window
    slot = next_upload_slots(1, last_slot=last_slot)[0]
    # Pass as naive local time — upload.py handles UTC conversion
    slot_naive = slot.replace(tzinfo=None)

    print(f"Video      : {video.name}")
    description, sound = asyncio.run(get_description(video))
    print(f"Description: {description}")
    print(f"Sound      : {sound or '(none)'}")
    print(f"Schedule   : {slot.strftime('%Y-%m-%d %H:%M %Z')} (local time)")
    print()
    print("Uploading…")

    uploader = TikTokUploader(cookies=str(COOKIES_FILE), headless=args.headless)
    success = uploader.upload_video(
        str(video), description=description, schedule=slot_naive, sound=sound
    )

    if success:
        state["uploaded"].append(video.name)
        state["last_slot"] = slot.isoformat()
        save_state(state)
        print(f"✓ Scheduled for {slot.strftime('%Y-%m-%d %H:%M %Z')} — state saved.")
    else:
        print("✗ Upload failed.")


if __name__ == "__main__":
    main()
