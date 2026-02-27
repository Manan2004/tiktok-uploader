"""
TikTok Uploader Initialization
"""

import logging
from os.path import abspath, dirname, join

from tiktok_uploader.settings import load_config

## Load Config
config_dir = abspath(dirname(__file__))
config = load_config(join(config_dir, "config.toml"))

## Setup Logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# No custom handler — let records propagate to the root logger
# so callers can configure formatting and filtering in one place.

from tiktok_uploader.upload import (
    TikTokUploader,
    upload_video,
    upload_videos,
)  # noqa: E402, I001

__all__ = ["TikTokUploader", "upload_video", "upload_videos"]
