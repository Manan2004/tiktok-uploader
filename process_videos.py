#!/usr/bin/env python3
"""
Scan ./videos, recognize songs via Shazam, then upload to TikTok
with appropriate hashtags.

  Song detected  → #SongName #ArtistName #SpeedRecords #TikTokVideos
                    #TimesMusic #TrendingSongs #HitSong #PunjabiTikTok
  No song found  → #SpeedRecords #TikTokVideos #PunjabiTikTok
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shazamio import Shazam
from tiktok_uploader.upload import TikTokUploader

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
VIDEOS_DIR = Path("videos")
COOKIES_FILE = Path("cookies.txt")
STATE_FILE = Path("uploaded.json")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm"}

MUSIC_TAGS = (
    "#SpeedRecords #TikTokVideos #TimesMusic #TrendingSongs #HitSong #PunjabiTikTok"
)
DEFAULT_TAGS = "#SpeedRecords #TikTokVideos #PunjabiTikTok"

# Upload window (local time)
WINDOW_START_HOUR = 9  # 9:00 AM
WINDOW_END_HOUR = 22  # 10:00 PM (last slot is 22:00)
SCHEDULE_BUFFER_MIN = 25  # TikTok requires ≥ 20 min in the future


# ---------------------------------------------------------------------------
# State persistence  (uploaded.json)
# ---------------------------------------------------------------------------
def load_state() -> dict:
    """Return {uploaded: [filename, ...], last_slot: ISO-str | None}."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {"uploaded": [], "last_slot": None}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------
def next_upload_slots(count: int, last_slot: datetime | None = None) -> list[datetime]:
    """
    Return *count* consecutive hourly slots (local time) inside the
    10:00–21:00 window, each at least SCHEDULE_BUFFER_MIN minutes from now
    AND strictly after *last_slot* (if provided) so re-runs never double-book.
    Slots land on the exact hour (e.g. 11:00, 12:00 …).
    """
    now = datetime.now().astimezone()  # local-aware datetime
    earliest = now + timedelta(minutes=SCHEDULE_BUFFER_MIN)

    # If a previous run already booked a slot, start one hour after it
    if last_slot is not None:
        earliest = max(earliest, last_slot + timedelta(hours=1))

    # Round up to the next whole hour
    if earliest.minute > 0 or earliest.second > 0 or earliest.microsecond > 0:
        candidate = earliest.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )
    else:
        candidate = earliest.replace(second=0, microsecond=0)

    # Push into the window if needed
    if candidate.hour < WINDOW_START_HOUR:
        candidate = candidate.replace(
            hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0
        )
    elif candidate.hour > WINDOW_END_HOUR:
        # Past the window — start at 10 AM tomorrow
        candidate = (candidate + timedelta(days=1)).replace(
            hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0
        )

    slots: list[datetime] = []
    while len(slots) < count:
        slots.append(candidate)
        candidate = candidate + timedelta(hours=1)
        # Wrap to next day if we've gone past the window
        if candidate.hour > WINDOW_END_HOUR:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0
            )

    return slots


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------
def extract_audio(video_path: Path, output_path: Path) -> bool:
    """Extract first 30 s of audio from *video_path* into a WAV at *output_path*."""
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(video_path),
                "-vn",  # strip video
                "-acodec",
                "pcm_s16le",
                "-ar",
                "44100",
                "-ac",
                "1",  # mono
                "-t",
                "30",  # 30 s is plenty for Shazam
                "-y",  # overwrite temp file if exists
                str(output_path),
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.debug("ffmpeg stderr: %s", result.stderr.decode(errors="replace"))
            return False
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("ffmpeg error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Shazam recognition
# ---------------------------------------------------------------------------
async def recognize_song(video_path: Path) -> dict | None:
    """Return the Shazam result dict for *video_path*, or None on failure."""
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".wav")
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        logger.info("  › extracting audio…")
        if not extract_audio(video_path, tmp_path):
            logger.warning("  › audio extraction failed — skipping Shazam")
            return None

        logger.info("  › querying Shazam…")
        shazam = Shazam()
        result = await shazam.recognize(str(tmp_path))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("  › Shazam error: %s", exc)
        return None
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------
def _clean_text(text: str) -> str:
    """Strip parenthetical/bracketed annotations and extra punctuation from song/artist text.

    e.g. 'Cute Jahi Smile (from "Ishqan De Lekhe")' → 'Cute Jahi Smile'
         'Lover [Official Video]'                    → 'Lover'
         "Song - feat. Artist"                       → 'Song   feat  Artist'
    """
    # Remove anything inside (), [], {} — handles nested quotes too
    text = re.sub(r"\s*[\(\[\{][^\)\]\}]*[\)\]\}]", "", text)
    # Remove stray punctuation left over (quotes, dashes at start/end)
    text = re.sub(r"""[\"\'`\-–—]+""", " ", text)
    return text.strip()


def _to_hashtag(text: str) -> str:
    """Convert arbitrary text to a single camelCase hashtag."""
    text = _clean_text(text)
    # Title-case each word, keep alphanumeric only
    words = text.replace("-", " ").replace("_", " ").split()
    return "#" + "".join(w.capitalize() for w in words if w)


def build_description(shazam_result: dict | None) -> str:
    """Return the TikTok description string based on Shazam result."""
    if shazam_result and "track" in shazam_result:
        track = shazam_result["track"]
        title: str = track.get("title", "").strip()
        artist: str = track.get("subtitle", "").strip()  # e.g. "Diljit Dosanjh"

        if title or artist:
            parts: list[str] = []

            if title:
                parts.append(_to_hashtag(title))

            if artist:
                # Split on separators (&, ,, ft., feat.) to keep each full
                # artist name together, then camelCase the whole name.
                # "Tarsem Jassar & Deep Jandu" → #TarsemJassar #DeepJandu
                artist_names = re.split(
                    r"\s*(?:&|,|ft\.|feat\.)\s*", artist, flags=re.IGNORECASE
                )
                for name in artist_names:
                    name = name.strip()
                    if name:
                        parts.append(_to_hashtag(name))

            parts.append(MUSIC_TAGS)
            return " ".join(parts)

    return DEFAULT_TAGS


# ---------------------------------------------------------------------------
# Phase 1 (async): recognise all songs, return jobs list
# ---------------------------------------------------------------------------
async def recognise_all(videos: list[Path]) -> list[tuple[Path, str]]:
    """Return list of (video_path, description) for every video."""
    jobs: list[tuple[Path, str]] = []
    for video in videos:
        logger.info("")
        logger.info("━" * 55)
        logger.info("Recognising: %s", video.name)
        result = await recognize_song(video)
        if result and "track" in result:
            track = result["track"]
            logger.info(
                "  ✓ Song: %s — %s", track.get("title", "?"), track.get("subtitle", "?")
            )
        else:
            logger.info("  ✗ No song recognised → using default tags")
        jobs.append((video, build_description(result)))
    return jobs


# ---------------------------------------------------------------------------
# Phase 2 (sync): upload with schedule — must run outside asyncio loop
# ---------------------------------------------------------------------------
def upload_all(
    jobs: list[tuple[Path, str]], slots: list[datetime], state: dict
) -> None:
    uploader = TikTokUploader(cookies=str(COOKIES_FILE), headless=True)
    failed: list[str] = []

    for (video, description), slot in zip(jobs, slots):
        logger.info("")
        logger.info("━" * 55)
        logger.info("Uploading : %s", video.name)
        logger.info("  Description : %s", description)
        logger.info("  Scheduled at: %s", slot.strftime("%Y-%m-%d %H:%M %Z"))

        # Pass as naive local datetime — upload.py treats naive as local and converts to UTC correctly.
        # Do NOT convert to UTC first; that causes a double-conversion (5 hr offset on EST).
        slot_naive = slot.replace(tzinfo=None)

        try:
            uploader.upload_video(
                str(video), description=description, schedule=slot_naive
            )
            logger.info("  ✓ Scheduled successfully")
            state["uploaded"].append(video.name)
            state["last_slot"] = slot.isoformat()
            save_state(state)
        except Exception as exc:  # noqa: BLE001
            logger.error("  ✗ Upload failed: %s", exc)
            failed.append(video.name)

    logger.info("")
    logger.info("━" * 55)
    logger.info("Done.  %d scheduled,  %d failed", len(jobs) - len(failed), len(failed))
    if failed:
        for name in failed:
            logger.info("  FAILED: %s", name)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    # --- sanity checks -------------------------------------------------------
    if not COOKIES_FILE.exists():
        logger.error(
            "cookies.txt not found!\n"
            "Export your TikTok cookies (NetScape format) and place them at:\n  %s",
            COOKIES_FILE.absolute(),
        )
        return

    VIDEOS_DIR.mkdir(exist_ok=True)
    all_videos = sorted(
        f for f in VIDEOS_DIR.iterdir() if f.suffix.lower() in VIDEO_EXTENSIONS
    )

    # --- load state and skip already-uploaded videos -------------------------
    state = load_state()
    already_done: set[str] = set(state.get("uploaded", []))
    last_slot_iso: str | None = state.get("last_slot")
    last_slot: datetime | None = (
        datetime.fromisoformat(last_slot_iso).astimezone() if last_slot_iso else None
    )

    videos = [v for v in all_videos if v.name not in already_done]
    skipped = len(all_videos) - len(videos)

    if skipped:
        logger.info("Skipping %d already-uploaded video(s).", skipped)

    if not videos:
        logger.info("No new videos in %s/ — add files and re-run.", VIDEOS_DIR)
        return

    logger.info("Found %d new video(s) in %s/", len(videos), VIDEOS_DIR)

    # --- compute slots -------------------------------------------------------
    slots = next_upload_slots(len(videos), last_slot=last_slot)
    logger.info("")
    logger.info("Scheduled upload slots (local time):")
    for i, (v, s) in enumerate(zip(videos, slots), 1):
        logger.info("  %d. %-30s → %s", i, v.name, s.strftime("%Y-%m-%d %H:%M %Z"))

    # --- phase 1: async song recognition -------------------------------------
    jobs = asyncio.run(recognise_all(videos))

    # --- phase 2: sync upload (Playwright sync API must be outside asyncio) --
    upload_all(jobs, slots, state)


if __name__ == "__main__":
    main()
