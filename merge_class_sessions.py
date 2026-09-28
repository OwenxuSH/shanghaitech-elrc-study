#!/usr/bin/env python3
"""Collapse consecutive ELRC recording segments into actual class meetings.

ELRC may expose one two-period class as separate recordings such as 5-5 and
6-6. This script preserves those source files, creates concatenated MP4/VTT
copies for multi-segment meetings, and rewrites the player manifest to one
resource per date/day meeting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
COURSES = ("CS182", "CS181", "CS280", "CS277")


def parse_section(value: Any) -> tuple[int, int]:
    match = re.search(r"(\d+)\s*-\s*(\d+)", str(value or ""))
    if not match:
        match = re.search(r"(\d+)", str(value or ""))
    if not match:
        return (10**9, 10**9)
    if match.lastindex == 1:
        number = int(match.group(1))
        return (number, number)
    return (int(match.group(1)), int(match.group(2)))


def section_label(items: list[dict[str, Any]]) -> str:
    starts = [parse_section(item.get("session", {}).get("section"))[0] for item in items]
    ends = [parse_section(item.get("session", {}).get("section"))[1] for item in items]
    first, last = min(starts), max(ends)
    return str(first) if first == last else f"{first}-{last}"


def timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def parse_vtt(text: str) -> list[tuple[float, float, list[str]]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[tuple[float, float, list[str]]] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^\s*(\S+)\s+-->\s+(\S+)(?:\s+.*)?$", lines[index])
        if not match:
            index += 1
            continue
        start, end = timestamp_seconds(match.group(1)), timestamp_seconds(match.group(2))
        index += 1
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index])
            index += 1
        if any(line.strip() for line in body):
            cues.append((start, end, body))
        index += 1
    return cues


def write_merged_vtt(paths: list[Path], offsets: list[float], destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    output = ["WEBVTT", ""]
    for path, offset in zip(paths, offsets):
        if not path.is_file():
            continue
        for start, end, body in parse_vtt(path.read_text(encoding="utf-8", errors="replace")):
            output.append(f"{format_timestamp(start + offset)} --> {format_timestamp(end + offset)}")
            output.extend(body)
            output.append("")
            count += 1
    destination.write_text("\n".join(output), encoding="utf-8")
    return count


def vtt_to_text(path: Path) -> str:
    cues = parse_vtt(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else []
    lines = [line for _, _, body in cues for line in body if line.strip()]
    return "\n".join(lines).strip() + ("\n" if lines else "")


def media_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def concat_video(paths: list[Path], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", delete=False, encoding="utf-8") as stream:
        list_path = Path(stream.name)
        for path in paths:
            escaped = str(path).replace("'", "'\\''")
            stream.write(f"file '{escaped}'\n")
    temporary = destination.with_name(destination.stem + ".part" + destination.suffix)
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(temporary),
            ],
            check=True,
        )
        os.replace(temporary, destination)
    finally:
        list_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def merge_course(course_number: str) -> None:
    course_root = ROOT / course_number
    manifest_path = course_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_backup = course_root / "manifest_segments.json"
    if manifest.get("aggregation") and source_backup.exists():
        source_manifest = json.loads(source_backup.read_text(encoding="utf-8"))
    else:
        shutil.copy2(manifest_path, source_backup)
        source_manifest = manifest
    source_resources = [
        item
        for item in source_manifest.get("resources", [])
        if item.get("seat") == "屏幕画面"
        and Path(item.get("video", {}).get("path", "")).is_file()
    ]
    expected_by_key: dict[tuple[Any, str, str], list[dict[str, Any]]] = {}
    for item in source_manifest.get("available_sessions", []) or []:
        session = {
            "week": item.get("week"),
            "week_day": item.get("week_day", ""),
            "week_date": item.get("week_date", ""),
            "section": item.get("section") or item.get("realSection"),
        }
        key = (session["week"], session["week_day"], session["week_date"])
        expected_by_key.setdefault(key, []).append({"session": session})
    groups: dict[tuple[Any, str, str], list[dict[str, Any]]] = {}
    for item in source_resources:
        session = item.get("session", {})
        key = (session.get("week"), session.get("week_day", ""), session.get("week_date", ""))
        groups.setdefault(key, []).append(item)

    merged_resources: list[dict[str, Any]] = []
    for key, items in sorted(groups.items(), key=lambda pair: (int(pair[0][0] or 0), pair[0][2], pair[0][1])):
        items.sort(key=lambda item: parse_section(item.get("session", {}).get("section")))
        session = dict(items[0].get("session", {}))
        expected_items = expected_by_key.get(key, [])
        combined_section = section_label(expected_items or items)
        expected_segment_count = len(expected_items) or len(items)
        session["section"] = combined_section
        week, week_day, week_date = key
        label = f"第{week}周_{week_day}_{combined_section}_{week_date}"
        label = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", label)
        video_paths = [Path(item["video"]["path"]) for item in items]
        durations = [media_duration(path) for path in video_paths]
        offsets: list[float] = []
        running = 0.0
        for duration in durations:
            offsets.append(running)
            running += duration

        if len(items) == 1:
            video_path = video_paths[0]
            source_transcripts = items[0].get("transcripts", {})
            transcript_results = source_transcripts
        else:
            merged_recording = course_root / "recordings" / "merged" / label / "屏幕画面.mp4"
            if (
                not merged_recording.exists()
                or merged_recording.stat().st_size == 0
                or any(path.stat().st_mtime > merged_recording.stat().st_mtime for path in video_paths)
            ):
                concat_video(video_paths, merged_recording)
            video_path = merged_recording
            transcript_results: dict[str, Any] = {}
            for language in ("zh", "en"):
                vtt_paths = [Path(item.get("transcripts", {}).get(language, {}).get("vtt", "")) for item in items]
                merged_vtt = course_root / "transcripts" / "merged" / label / f"屏幕画面_{language}.vtt"
                cue_count = write_merged_vtt(vtt_paths, offsets, merged_vtt)
                merged_txt = merged_vtt.with_suffix(".txt")
                merged_txt.write_text(vtt_to_text(merged_vtt), encoding="utf-8")
                transcript_results[language] = {
                    "vtt": str(merged_vtt),
                    "txt": str(merged_txt),
                    "bytes": merged_vtt.stat().st_size,
                    "segments": cue_count,
                }

        content_ids = [item.get("content_id") for item in items]
        merged_resources.append(
            {
                "content_id": "+".join(str(value) for value in content_ids if value),
                "content_ids": content_ids,
                "name": f"{manifest.get('course', {}).get('name', course_number)} 屏幕画面",
                "seat": "屏幕画面",
                "session": session,
                "expected_segments": expected_segment_count,
                "segments": [
                    {
                        "content_id": item.get("content_id"),
                        "section": item.get("session", {}).get("section"),
                        "video": item.get("video", {}).get("path"),
                        "duration": duration,
                    }
                    for item, duration in zip(items, durations)
                ],
                "video": {
                    "path": str(video_path),
                    "bytes": video_path.stat().st_size,
                    "duration": running,
                    "segments": len(items),
                },
                "transcripts": transcript_results,
            }
        )

    manifest["resources"] = merged_resources
    manifest["aggregation"] = {
        "mode": "actual_class_meeting",
        "group_key": ["week", "week_day", "week_date"],
        "source_manifest": str(source_backup),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{course_number}: {len(source_resources)} 个分段 -> {len(merged_resources)} 个实际课堂")
    for item in merged_resources:
        session = item["session"]
        print(
            f"  第{session.get('week')}周 {session.get('week_day')} "
            f"{session.get('section')}节 / {item['video'].get('segments')}段"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("courses", nargs="*", default=list(COURSES))
    args = parser.parse_args()
    for course in args.courses:
        merge_course(course.upper())


if __name__ == "__main__":
    main()
