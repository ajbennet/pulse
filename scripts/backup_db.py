"""
Back up the local PULSE database to Google Drive (or any folder).

The database at data/pulse.db holds personal financial records and is
gitignored, so it is never in the repo. Run this occasionally to keep
timestamped copies in your **private** Google Drive.

Two upload methods
------------------
1. rclone (recommended — uploads directly to Google Drive, no Drive Desktop):
       brew install rclone           # or see https://rclone.org/downloads/
       rclone config                 # create a remote named e.g. "gdrive" (type: drive)
   then:
       python scripts/backup_db.py --rclone gdrive:PULSE_backups
       # or set PULSE_RCLONE_REMOTE=gdrive:PULSE_backups and just run the script

2. Folder copy (if you use Google Drive for Desktop): copy the DB into the
   local sync folder and Drive uploads it. Auto-detected, or pass --dest.

Usage
-----
    python scripts/backup_db.py                       # auto: rclone if configured, else folder
    python scripts/backup_db.py --rclone gdrive:PULSE_backups
    python scripts/backup_db.py --dest "/path/to/dir"
    PULSE_RCLONE_REMOTE="gdrive:PULSE_backups" python scripts/backup_db.py
    python scripts/backup_db.py --keep 50             # keep the newest 50 copies

Schedule it (optional), e.g. daily at 6pm via cron:
    0 18 * * *  cd /path/to/pulse && python scripts/backup_db.py >> logs/backup.log 2>&1
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime

DEFAULT_DB = os.path.join("data", "pulse.db")
BACKUP_SUBDIR = "PULSE_backups"


def find_gdrive_root():
    """Best-effort detection of a Google Drive Desktop sync folder."""
    home = os.path.expanduser("~")
    candidates = []
    # macOS "Google Drive for desktop"
    candidates += glob.glob(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-*", "My Drive"))
    candidates += glob.glob(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-*"))
    # Windows / older clients
    candidates += [os.path.join(home, "Google Drive", "My Drive"),
                   os.path.join(home, "Google Drive"),
                   os.path.join("G:\\", "My Drive")]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def resolve_dest(explicit):
    if explicit:
        return explicit
    env = os.environ.get("PULSE_BACKUP_DIR")
    if env:
        return env
    root = find_gdrive_root()
    if root:
        return os.path.join(root, BACKUP_SUBDIR)
    return None


def _have_rclone():
    return shutil.which("rclone") is not None


def backup_via_rclone(db, remote, keep):
    if not _have_rclone():
        sys.exit("rclone not installed. `brew install rclone` then `rclone config`.")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = f"{remote.rstrip('/')}/pulse_{ts}.db"
    subprocess.run(["rclone", "copyto", db, target], check=True)
    print(f"Backed up {db} -> {target} (via rclone)")
    if keep > 0:
        # List remote backups and delete the oldest beyond `keep`.
        listing = subprocess.run(["rclone", "lsf", remote], capture_output=True, text=True)
        files = sorted(f for f in listing.stdout.splitlines() if f.startswith("pulse_"))
        for old in files[:-keep]:
            subprocess.run(["rclone", "deletefile", f"{remote.rstrip('/')}/{old}"], check=False)
            print(f"Pruned old backup: {old}")


def backup_via_folder(db, dest, keep):
    os.makedirs(dest, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(dest, f"pulse_{ts}.db")
    shutil.copy2(db, out)
    print(f"Backed up {db} -> {out} ({os.path.getsize(out) / 1024:.0f} KB)")
    if keep > 0:
        for old in sorted(glob.glob(os.path.join(dest, "pulse_*.db")))[:-keep]:
            os.remove(old)
            print(f"Pruned old backup: {old}")


def main():
    ap = argparse.ArgumentParser(description="Back up data/pulse.db to Google Drive.")
    ap.add_argument("--db", default=DEFAULT_DB, help="Path to the database file.")
    ap.add_argument("--rclone", default=os.environ.get("PULSE_RCLONE_REMOTE"),
                    help="rclone remote:path, e.g. gdrive:PULSE_backups")
    ap.add_argument("--dest", default=None, help="Destination directory (folder mode).")
    ap.add_argument("--keep", type=int, default=30, help="How many backups to retain.")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"Database not found: {args.db}")

    # Prefer rclone if a remote is configured; otherwise fall back to a folder.
    if args.rclone:
        backup_via_rclone(args.db, args.rclone, args.keep)
        return

    dest = resolve_dest(args.dest)
    if not dest:
        sys.exit("No destination found. Use rclone (`--rclone gdrive:PULSE_backups` or "
                 "set PULSE_RCLONE_REMOTE), install Google Drive Desktop, or pass --dest.")
    backup_via_folder(args.db, dest, args.keep)


if __name__ == "__main__":
    main()
