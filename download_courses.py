#!/usr/bin/env python3
"""Download authenticated ShanghaiTech ELRC screen recordings and VTT captions.

The script obtains cookies from the visible, already-authenticated Chrome CDP
session. It never reads or writes a username/password. Existing MP4 files are
resumed with HTTP Range requests, so interrupted runs can be safely repeated.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import websocket


BASE = "https://elrc.shanghaitech.edu.cn"
CDP_PORT = int(os.environ.get("ELRC_CDP_PORT", "9224"))
CDP_LIST = f"http://127.0.0.1:{CDP_PORT}/json/list"
OUT_ROOT = Path(__file__).resolve().parent
SEMESTER_LABEL = os.environ.get("ELRC_SEMESTER", "2026-2027学年秋季")
SCHOOL_YEAR = SEMESTER_LABEL[:9]
SEMESTER = "秋季"
COURSE_NUMBERS = ("CS182", "CS181", "CS280", "CS277")
PREFERRED_SERIALS = {"CS182": "CS182.02"}


def cdp_cookies() -> list[dict[str, Any]]:
    pages = json.load(urllib.request.urlopen(CDP_LIST, timeout=10))
    page = next(
        (
            item
            for item in pages
            if item.get("type") == "page"
            and item.get("url", "").startswith(BASE + "/learn")
        ),
        None,
    )
    if not page:
        raise RuntimeError(f"找不到已登录的 ELRC Chrome 页面（CDP {CDP_PORT}）。")
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies", "params": {}}))
        while True:
            result = json.loads(ws.recv())
            if result.get("id") == 1:
                return result["result"]["cookies"]
    finally:
        ws.close()


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153 Safari/537.36"
            ),
            "Referer": BASE + "/learn/",
        }
    )
    for cookie in cdp_cookies():
        if cookie.get("domain", "").endswith("shanghaitech.edu.cn"):
            session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )
    return session


def api_get(session: requests.Session, path: str) -> Any:
    response = session.get(BASE + path, timeout=90)
    response.raise_for_status()
    data = response.json()
    if data.get("status") not in (None, 200) and data.get("success") is not True:
        raise RuntimeError(f"GET {path}: {data}")
    return data


def api_post(session: requests.Session, path: str, body: Any) -> Any:
    response = session.post(BASE + path, json=body, timeout=90)
    response.raise_for_status()
    data = response.json()
    if data.get("status") not in (None, 200) and data.get("success") is not True:
        raise RuntimeError(f"POST {path}: {data}")
    return data


def safe_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" .")
    return value or "未命名"


def vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    for line in vtt.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line)
        lines.append(line)
    return "\n".join(lines).strip() + ("\n" if lines else "")


def numeric_bytes(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def download(session: requests.Session, url: str, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = destination.stat().st_size if destination.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    response = session.get(url, headers=headers, stream=True, timeout=(30, 180))
    if existing and response.status_code == 416:
        return {"path": str(destination), "bytes": existing, "url": url}
    response.raise_for_status()
    append = existing > 0 and response.status_code == 206
    if not append:
        existing = 0
    mode = "ab" if append else "wb"
    total = existing + int(response.headers.get("Content-Length", "0"))
    written = existing
    last_report = -1
    with destination.open(mode) as stream:
        for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
            if not chunk:
                continue
            stream.write(chunk)
            written += len(chunk)
            if total:
                percent = int(written * 100 / total)
                if percent >= last_report + 5:
                    print(
                        f"    {destination.name}: {percent}% ({written / 1024**2:.1f} MiB)",
                        flush=True,
                    )
                    last_report = percent
    return {"path": str(destination), "bytes": written, "url": url}


def video_url(session: requests.Session, resource: dict[str, Any], meta: dict[str, Any]) -> str | None:
    addresses = meta.get("downloadAddress") or []
    if isinstance(addresses, str):
        addresses = [addresses]
    if addresses:
        address = str(addresses[0])
        return address if address.startswith("http") else BASE + address

    # The browser may omit a playable screen feed from fileinfo. Its media
    # route follows the same date/room/content-ID layout as adjacent feeds.
    start = str(resource.get("course_begin") or "").split(" ", 1)[0]
    room = str(resource.get("classroom_number") or "")
    content_id = str(resource.get("contentId_") or "")
    if not start or not room or not content_id:
        return None
    try:
        year, month, day = (int(part) for part in start.split("-"))
    except (ValueError, TypeError):
        return None
    path = (
        f"/bucket-z/unit-cwcc268v239qk92m/video/{year}/{month}/{day}/"
        f"{urllib.parse.quote(room, safe='')}/{content_id}.mp4"
    )
    response = session.get(BASE + path, headers={"Range": "bytes=0-1023"}, stream=True, timeout=20)
    try:
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes 0-\d+/(\d+)", content_range)
        declared = numeric_bytes(resource.get("filesize"))
        if response.status_code != 206 or not match or not response.headers.get("Content-Type", "").startswith("video/mp4"):
            return None
        if declared is not None and int(match.group(1)) != declared:
            return None
        print(f"  fileinfo 未列出 {content_id}，已验证页面媒体地址。", flush=True)
        return BASE + path
    finally:
        response.close()


def find_course(
    session: requests.Session, course_number: str, preferred_serial: str | None = None
) -> dict[str, Any]:
    params = {
        "page": 1,
        "size": 100,
        "college": "",
        "initial": "",
        "semester": SEMESTER_LABEL,
        "keyword": course_number,
    }
    data = api_get(
        session,
        "/learn/shanghai/tech/get/course?" + urllib.parse.urlencode(params),
    )
    results = data.get("data", {}).get("results", []) or []
    matches = [item for item in results if item.get("courseNumber") == course_number]
    match = next((item for item in matches if item.get("serialNumber") == preferred_serial), None)
    if match is None and matches:
        match = matches[0]
    if not match:
        raise RuntimeError(f"课程中心找不到 {course_number}（学期：{SEMESTER_LABEL}）。")
    return match


def find_back_info(session: requests.Session, course_number: str, serial_number: str) -> dict[str, Any]:
    body = [
        {
            "semester": SEMESTER_LABEL,
            "courseNumber": course_number,
            "serialNumber": serial_number,
        }
    ]
    data = api_post(session, "/learn/shanghai/tech/get/back/course", body)
    back_infos = data.get("data", {}).get("backInfos", []) or []
    match = next(
        (
            item
            for item in back_infos
            if item.get("courseNumber") == course_number
            and item.get("serialNumber") == serial_number
        ),
        None,
    )
    if not match:
        raise RuntimeError(f"课程 {course_number} 找不到录播后台入口。")
    return match


def collect_sessions(session: requests.Session, course: dict[str, Any], back: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    path = (
        "/learn/v1/course/recording/video/info?"
        + urllib.parse.urlencode(
            {
                # The recording endpoint uses the back-end course ID returned
                # by get/back/course, not the course-center content ID.
                "courseId": back["courseId"],
                "id": back["id"],
                "schoolYear": SCHOOL_YEAR,
                "semester": SEMESTER,
            }
        )
    )
    info = api_get(session, path)
    sessions: list[dict[str, Any]] = []
    for week_info in info.get("data", {}).get("recordingVideoInfoShows", []) or []:
        for detail in week_info.get("recordInfoDetailList", []) or []:
            videos = detail.get("videoInfoList", []) or []
            if not videos:
                continue
            schedule_ids = list(dict.fromkeys(v.get("scheduleId") for v in videos if v.get("scheduleId")))
            sessions.append(
                {
                    "week": week_info.get("week"),
                    "week_start": week_info.get("startTime"),
                    "week_end": week_info.get("endTime"),
                    "week_day": detail.get("weekDay"),
                    "week_date": detail.get("weekDate"),
                    "section": detail.get("section") or detail.get("realSection"),
                    "schedule_ids": schedule_ids,
                    "raw_video_info": videos,
                }
            )
    return info, sessions


def session_dir(item: dict[str, Any]) -> str:
    return safe_name(
        f"第{item.get('week', '?')}周_{item.get('week_day', '')}_"
        f"{item.get('section', '')}_{item.get('week_date', '')}"
    )


def resource_seat(meta: dict[str, Any], resource: dict[str, Any]) -> str:
    return str(
        meta.get("seat")
        or resource.get("seat")
        or meta.get("entityName")
        or resource.get("name")
        or ""
    )


def expected_file_size(meta: dict[str, Any]) -> Any:
    groups = meta.get("fileGroups") or []
    if groups and groups[0].get("files"):
        return groups[0]["files"][0].get("fileSize")
    return None


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def download_course(
    session: requests.Session, course_number: str, dry_run: bool = False, missing_only: bool = False
) -> None:
    course = find_course(session, course_number, PREFERRED_SERIALS.get(course_number))
    serial = course.get("serialNumber") or f"{course_number}.01"
    back = find_back_info(session, course_number, serial)
    info, sessions = collect_sessions(session, course, back)
    course_root = OUT_ROOT / course_number
    course_root.mkdir(parents=True, exist_ok=True)
    player_manifest_path = course_root / "manifest.json"
    current_manifest = json.loads(player_manifest_path.read_text(encoding="utf-8")) if player_manifest_path.exists() else {}
    if current_manifest.get("aggregation"):
        manifest_path = course_root / "manifest_segments.json"
    else:
        manifest_path = player_manifest_path
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if missing_only and previous.get("available_sessions"):
        allowed = {
            (item.get("week"), item.get("week_day"), item.get("week_date"), item.get("section"))
            for item in previous["available_sessions"]
        }
        sessions = [
            item for item in sessions
            if (item.get("week"), item.get("week_day"), item.get("week_date"), item.get("section")) in allowed
        ]
    if not missing_only:
        (course_root / "course_recording_info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    manifest: dict[str, Any] = {
        "course": {
            "id": course.get("contentId_"),
            "number": course_number,
            "name": course.get("name_") or course.get("name"),
            "serial_number": serial,
            "semester": SEMESTER_LABEL,
            "back_id": back.get("id"),
        },
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "available_sessions": previous.get("available_sessions", sessions) if missing_only else sessions,
        "resources": [],
    }
    manifest["resources"] = previous.get("resources", [])
    existing_resources = {item.get("content_id"): item for item in manifest["resources"]}

    all_resources: dict[tuple[str, Any, str, str], dict[str, Any]] = {}
    for item in sessions:
        payload = {
            "courseId": course_number,
            "courseNo": serial,
            "week": item["week"],
            "weekDay": item["week_day"],
            "section": item["section"],
            "scheduleIds": item["schedule_ids"] * 5,
            "isGetCut": True,
        }
        relation = api_post(session, "/rman/v1/search/new/relation/videos", payload)
        resources = relation.get("data", {}).get("data", []) or []
        for resource in resources:
            content_id = resource.get("contentId_")
            if content_id:
                key = (content_id, item.get("week"), item.get("week_day", ""), item.get("section", ""))
                all_resources[key] = {**resource, "session": item}

    if not all_resources:
        print(f"{course_number}: 没有返回可下载的录播资源。", flush=True)
        return

    # This endpoint expects the relation resources' content IDs, not the
    # complete relation objects.
    if missing_only:
        all_resources = {key: item for key, item in all_resources.items() if key[0] not in existing_resources}
    if not all_resources:
        print(f"{course_number}: 目标课堂没有缺失的屏幕资源。", flush=True)
        return
    fileinfo = api_post(session, "/rman/v1/entity/download/fileinfo", [key[0] for key in all_resources])
    file_by_id = {item.get("contentId"): item for item in fileinfo.get("data", []) or []}
    screen_resources = []
    for resource in all_resources.values():
        cid = resource.get("contentId_")
        meta = file_by_id.get(cid)
        seat = resource_seat(meta or {}, resource)
        if "屏幕" in seat:
            screen_resources.append((resource, meta or {}, seat))

    print(
        f"{course_number} {course.get('name_') or course.get('name')}: "
        f"可用课堂 {len(sessions)}，关系资源 {len(all_resources)}，屏幕画面 {len(screen_resources)}",
        flush=True,
    )
    if dry_run:
        for resource, meta, seat in screen_resources:
            item = resource["session"]
            print(
                f"  第{item.get('week')}周 {item.get('week_day')} {item.get('section')} "
                f"{seat} {expected_file_size(meta) or '?'}",
                flush=True,
            )
        return

    for resource, meta, seat in screen_resources:
        content_id = resource.get("contentId_")
        resolved_url = video_url(session, resource, meta)
        if not resolved_url:
            print(f"  跳过 {content_id}: 没有可验证的视频地址", flush=True)
            continue
        item = resource["session"]
        directory = session_dir(item)
        video_path = course_root / "recordings" / directory / f"{safe_name(seat)}.mp4"
        print(f"  下载视频：{directory} / {seat}", flush=True)
        video_result = download(session, resolved_url, video_path)
        declared_size = numeric_bytes(resource.get("filesize"))
        if declared_size is not None and video_result["bytes"] != declared_size:
            raise RuntimeError(f"{content_id}: 下载大小不符，得到 {video_result['bytes']}，预期 {declared_size}")
        transcript_results: dict[str, Any] = {}
        transcript_dir = course_root / "transcripts" / directory
        transcript_dir.mkdir(parents=True, exist_ok=True)
        for lang, suffix in (("source", "zh"), ("en", "en")):
            response = session.get(
                BASE + "/rman/v1/search/webvtt",
                params={"contentId": content_id, "voice": "true", "isSysAuth": "true", "lang": lang},
                timeout=90,
            )
            response.raise_for_status()
            vtt = response.text
            vtt_path = transcript_dir / f"{safe_name(seat)}_{suffix}.vtt"
            txt_path = transcript_dir / f"{safe_name(seat)}_{suffix}.txt"
            vtt_path.write_text(vtt, encoding="utf-8")
            txt_path.write_text(vtt_to_text(vtt), encoding="utf-8")
            transcript_results[suffix] = {
                "vtt": str(vtt_path),
                "txt": str(txt_path),
                "bytes": len(response.content),
                "segments": max(0, vtt.count(" --> ")),
            }
        record = {
            "content_id": content_id,
            "name": meta.get("entityName") or resource.get("name"),
            "seat": seat,
            "session": {k: item.get(k) for k in ("week", "week_day", "week_date", "section")},
            "file_size": expected_file_size(meta) or resource.get("filesize"),
            "video": video_result,
            "transcripts": transcript_results,
        }
        existing_resources[content_id] = record
        manifest["resources"] = list(existing_resources.values())
        save_manifest(manifest_path, manifest)
    if current_manifest.get("aggregation"):
        from merge_class_sessions import merge_course

        merge_course(course_number)
    print(f"{course_number}: 完成，清单 {manifest_path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只解析资源，不下载视频和转写")
    parser.add_argument("--missing-only", action="store_true", help="只补现有课程清单里缺少的分段")
    parser.add_argument("courses", nargs="*", default=list(COURSE_NUMBERS))
    args = parser.parse_args()
    selected = [item.upper() for item in args.courses]
    unknown = [item for item in selected if item not in COURSE_NUMBERS]
    if unknown:
        raise SystemExit(f"未知课程：{', '.join(unknown)}")
    session = make_session()
    print(f"已从 CDP {CDP_PORT} 读取上海科技大学会话 Cookie（仅内存使用）。", flush=True)
    for course_number in selected:
        try:
            download_course(session, course_number, dry_run=args.dry_run, missing_only=args.missing_only)
        except Exception as exc:
            print(f"{course_number}: 失败：{exc}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断；再次运行会从已有视频继续。", file=sys.stderr)
        raise SystemExit(130)
