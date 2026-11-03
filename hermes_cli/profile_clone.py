"""Staging helpers for atomic profile publication."""
import os
from pathlib import Path
import shutil


def clone_staging_dir(profile_dir: Path) -> Path:
    """Fresh ``profiles/.<name>.staging-<pid>`` beside the final dir (same filesystem, so the publish
    rename is atomic). A leftover from a crashed create is discarded."""
    staging = profile_dir.parent / f".{profile_dir.name}.staging-{os.getpid()}"
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    if staging.is_symlink() or staging.is_file():
        staging.unlink()
    elif staging.is_dir():
        shutil.rmtree(staging, ignore_errors=True)
    return staging
