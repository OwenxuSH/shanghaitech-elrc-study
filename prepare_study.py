#!/usr/bin/env python3
"""Rebuild selected slide screenshots from a student's own ELRC recordings."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
COURSES = ("CS181", "CS182", "CS277", "CS280")
PAGE_LINK = re.compile(r"\]\((page_\d+\.jpg)\)")


def lesson_slug(session: dict) -> str:
    return f"第{session['week']}周_{session['week_day']}_{session['section']}_{session['week_date']}"


def local_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def prepare_course(code: str) -> tuple[int, int]:
    manifest_path = ROOT / code / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"{manifest_path}: 先通过 ELRC 下载录播")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lessons = images = 0
    for resource in manifest.get("resources", []):
        if resource.get("seat") != "屏幕画面":
            continue
        folder = ROOT / code / "notes" / lesson_slug(resource["session"])
        index_path = folder / "pages.json"
        note_path = folder / "课堂笔记.md"
        if not index_path.is_file() or not note_path.is_file():
            continue
        video = local_path(resource["video"]["path"])
        if not video.is_file():
            raise FileNotFoundError(f"{video}: 对应课堂录播尚未下载或合并")
        pages = {item["image"]: item for item in json.loads(index_path.read_text(encoding="utf-8"))["pages"]}
        selected = list(dict.fromkeys(PAGE_LINK.findall(note_path.read_text(encoding="utf-8"))))
        lessons += 1
        added = 0
        for name in selected:
            if name not in pages:
                raise ValueError(f"{index_path}: 找不到页面 {name}")
            destination = folder / name
            if destination.is_file():
                continue
            subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", str(pages[name]["sample"]),
                 "-i", str(video), "-frames:v", "1", "-q:v", "3", "-y", str(destination)],
                check=True,
            )
            images += 1
            added += 1
        print(f"{code} {folder.name}: {len(selected)} 个页面，新增 {added} 张截图", flush=True)
    return lessons, images


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("courses", nargs="*", choices=COURSES, default=list(COURSES))
    args = parser.parse_args()
    total_lessons = total_images = 0
    for code in args.courses:
        lessons, images = prepare_course(code)
        total_lessons += lessons
        total_images += images
    print(f"完成：{total_lessons} 节课堂，新增 {total_images} 张截图")


if __name__ == "__main__":
    main()
