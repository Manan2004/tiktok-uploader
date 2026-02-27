#!/usr/bin/env python3
"""
Single-video upload test — uses the same 9AM-10PM slot logic as process_videos.py.
Usage:  uv run python test_upload.py videos/YOUR_VIDEO.mp4
"""
import asyncio
import sys
from pathlib import Path

from process_videos import (
    recognize_song,
    build_description,
    next_upload_slots,
    load_state,
    save_state,
    COOKIES_FILE,
)
from tiktok_uploader.upload import TikTokUploader
from datetime import datetime


async def get_description(video: Path) -> str:
    result = await recognize_song(video)
    if result and "track" in result:
        track = result["track"]
        print(f"  Song  : {track.get('title', '?')}")
        print(f"  Artist: {track.get('subtitle', '?')}")
    else:
        print("  No song detected → using default tags")
    return build_description(result)


def main() -> None:
    if len(sys.argv) < 2:
        videos = sorted(Path("videos").glob("*.mp4"))
        if not videos:
            print(
                "No video found. Pass a path: uv run python test_upload.py videos/X.mp4"
            )
            return
        video = videos[0]
    else:
        video = Path(sys.argv[1])

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
    description = asyncio.run(get_description(video))
    print(f"Description: {description}")
    print(f"Schedule   : {slot.strftime('%Y-%m-%d %H:%M %Z')} (local time)")
    print()
    print("Uploading…")

    uploader = TikTokUploader(cookies=str(COOKIES_FILE), headless=True)
    success = uploader.upload_video(
        str(video), description=description, schedule=slot_naive
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
