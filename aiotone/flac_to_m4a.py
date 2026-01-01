#!/usr/bin/env python3
"""Encode FLAC to AAC using Apple Compressor and copy metadata.

Can also copy metadata from FLAC to an existing M4A file.

Tag mapping from FLAC to M4A (discovered from Ocenaudio export):
  FLAC             M4A
  ----             ---
  title         → ©nam
  artist        → ©ART
  album         → ©alb
  composer      → ©wrt
  comment       → ©cmt
  genre         → ©gen
  date/year     → ©day
  tracknumber   → trkn (tuple with tracktotal)
  tracktotal    → trkn (tuple with tracknumber)
  bpm           → tmpo
  picture       → covr
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4Cover

COMPRESSOR_BIN = Path("/Applications/Compressor.app/Contents/MacOS/Compressor")
COMPRESSOR_SETTINGS_DIR = Path(
    "~/Library/Group Containers/PTN9T2S29T.com.apple.videoProApps"
    "/Library/Application Support/Compressor/Settings"
).expanduser()
DEFAULT_PRESET = "AAC VBRC 288kbps Max.compressorsetting"

# note: if you run
# $ codesign -dv /Applications/Compressor.app
# you'll get PTN9T2S29T as the "TeamIdentifier" for Compressor


def _parse_compressor_json_stream(output: str) -> list[dict]:
    """Parse Compressor's JSON output stream to a list of dicts.

    Compressor outputs multiple JSON documents separated by unindented [ and ]
    on their own lines. This function parses all documents and returns the
    non-empty ones.
    """
    documents = []
    current_doc = []
    in_document = False

    for line in output.splitlines(keepends=True):
        if line == "[\n" or line == "[":
            in_document = True
            current_doc = [line]
        elif line == "]\n" or line == "]":
            if in_document:
                current_doc.append(line)
                doc_str = "".join(current_doc)
                try:
                    parsed = json.loads(doc_str)
                    if parsed:  # Skip empty arrays
                        documents.append(parsed)
                except json.JSONDecodeError:
                    pass
                current_doc = []
                in_document = False
        elif in_document:
            current_doc.append(line)

    return documents


def copy_metadata(flac_path: Path, m4a_path: Path) -> None:
    """Copy metadata and album art from FLAC to M4A."""
    flac = FLAC(flac_path)
    m4a = MP4(m4a_path)

    tag_map = {
        "title": "©nam",
        "artist": "©ART",
        "album": "©alb",
        "composer": "©wrt",
        "comment": "©cmt",
        "genre": "©gen",
    }

    for flac_tag, m4a_tag in tag_map.items():
        if flac_tag in flac:
            m4a[m4a_tag] = flac[flac_tag]

    # Date: prefer 'date', fall back to 'year'
    if "date" in flac:
        m4a["©day"] = flac["date"]
    elif "year" in flac:
        m4a["©day"] = flac["year"]

    # Track number and total
    track_num = int(flac.get("tracknumber", ["0"])[0]) if "tracknumber" in flac else 0
    track_total = int(flac.get("tracktotal", flac.get("totaltracks", ["0"]))[0])
    if track_num or track_total:
        m4a["trkn"] = [(track_num, track_total)]

    # BPM (tempo)
    if "bpm" in flac:
        m4a["tmpo"] = [int(flac["bpm"][0])]

    # Album art
    if flac.pictures:
        covers = []
        for pic in flac.pictures:
            if pic.mime == "image/png":
                img_format = MP4Cover.FORMAT_PNG
            else:
                img_format = MP4Cover.FORMAT_JPEG
            covers.append(MP4Cover(pic.data, imageformat=img_format))
        if covers:
            m4a["covr"] = covers

    m4a.save()
    print(f"Copied metadata from {flac_path.name} to {m4a_path.name}")


def encode_with_compressor(
    flac_path: Path,
    output_path: Path,
    preset: str,
    poll_interval: float = 0.5,
) -> bool:
    """Encode FLAC to M4A using Apple Compressor CLI. Returns True on success."""
    if not COMPRESSOR_BIN.exists():
        print(f"error: Apple Compressor not found at {COMPRESSOR_BIN}")
        return False

    preset_path = COMPRESSOR_SETTINGS_DIR / preset
    if not preset_path.exists():
        print(f"error: Preset not found: {preset_path}")
        print(f"Available presets in {COMPRESSOR_SETTINGS_DIR}:")
        for p in COMPRESSOR_SETTINGS_DIR.glob("*.compressorsetting"):
            print(f"  {p.name}")
        return False

    # Submit the job
    cmd = [
        str(COMPRESSOR_BIN),
        "-outputformat",
        "json",
        "-batchname",
        f"Encode {flac_path.stem}",
        "-jobpath",
        str(flac_path.resolve()),
        "-settingpath",
        str(preset_path),
        "-locationpath",
        str(output_path.resolve()),
    ]

    print(f"Submitting to Compressor: {flac_path.name} → {output_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error submitting job: {result.stderr}")
        return False

    # Parse JSON output to get batch ID
    try:
        # Output has a log line before JSON, find the JSON part
        json_start = result.stdout.find("{")
        if json_start == -1:
            print(f"error: No JSON in output: {result.stdout}")
            return False
        json_data = json.loads(result.stdout[json_start:])
        batch_id = json_data["batch"]["batchID"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing Compressor output: {e}")
        print(f"Output was: {result.stdout}")
        return False

    print(f"Job submitted (batch {batch_id}), waiting for completion...")

    # Poll for completion
    while True:
        monitor_cmd = [
            str(COMPRESSOR_BIN),
            "-monitor",
            "-batchid",
            batch_id,
            "-outputformat",
            "json",
            "-once",
        ]
        monitor_result = subprocess.run(monitor_cmd, capture_output=True, text=True)

        if monitor_result.returncode != 0:
            print(f"Error monitoring job: {monitor_result.stderr}")
            return False

        try:
            documents = _parse_compressor_json_stream(monitor_result.stdout)
            if not documents:
                # Job might still be queuing, wait and try again
                time.sleep(poll_interval)
                continue
            # Use the last non-empty document
            status_data = documents[-1]
            batch_status = status_data[0]["batchStatus"]
            status = batch_status["status"]
            percent = batch_status.get("percentComplete", "0")

            if status == "Successful":
                print(f"Encoding complete: {output_path.name}")
                return True
            elif status in ("Failed", "Canceled"):
                print(f"Encoding {status.lower()}")
                return False
            else:
                print(f"  Progress: {percent}%", end="\r")
                time.sleep(poll_interval)
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"Error parsing monitor output: {e}")
            return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Encode FLAC to AAC with Apple Compressor and copy metadata."
    )
    parser.add_argument("flac", type=Path, help="Source FLAC file")
    parser.add_argument("m4a", type=Path, help="Destination M4A file")
    parser.add_argument(
        "--encode",
        action="store_true",
        help="Encode FLAC to M4A using Apple Compressor (otherwise just copy metadata)",
    )
    parser.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        help=f"Compressor preset name (default: {DEFAULT_PRESET})",
    )
    args = parser.parse_args()

    if not args.flac.exists():
        print(f"error: FLAC file not found: {args.flac}")
        return 1

    if args.encode:
        if args.m4a.exists():
            print(f"error: Output file already exists: {args.m4a}")
            print("Remove it first or use a different output path.")
            return 1

        if not encode_with_compressor(args.flac, args.m4a, args.preset):
            return 1

    if not args.m4a.exists():
        print(f"error: M4A file not found: {args.m4a}")
        return 1

    copy_metadata(args.flac, args.m4a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
