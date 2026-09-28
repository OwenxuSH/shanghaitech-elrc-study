#!/usr/bin/env python3
"""Local, read-only video-first study service for the four ELRC courses."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "study_web"
COURSES = ("CS181", "CS182", "CS277", "CS280")


def safe_file(path: Path) -> Path | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(ROOT)
        return resolved if resolved.is_file() else None
    except (OSError, ValueError):
        return None


def file_url(path: Path | str | None) -> str | None:
    if not path:
        return None
    file_path = safe_file(Path(path))
    if not file_path:
        return None
    return "/media/" + quote(file_path.relative_to(ROOT).as_posix(), safe="/")


def session_slug(session: dict) -> str:
    return f"第{session['week']}周_{session['week_day']}_{session['section']}_{session['week_date']}"


def catalog() -> dict:
    courses = []
    for code in COURSES:
        data = json.loads((ROOT / code / "manifest.json").read_text(encoding="utf-8"))
        lessons = []
        for item in data.get("resources", []):
            if item.get("seat") != "屏幕画面" or not item.get("video", {}).get("path"):
                continue
            session = item["session"]
            slug = session_slug(session)
            index = ROOT / code / "notes" / slug / "pages.json"
            if not index.is_file() or not safe_file(Path(item["video"]["path"])):
                continue
            lessons.append({
                "id": slug,
                "title": f"第{session['week']}周 {session['week_day']} {session['section']}节 · {session['week_date']}",
                "date": session["week_date"],
            })
        lessons.sort(key=lambda x: x["date"])
        courses.append({"code": code, "name": data.get("course", {}).get("name", code), "lessons": lessons})
    return {"courses": courses}


def flat_lessons() -> dict:
    """Compatibility endpoint for local ELRC player tooling."""
    lessons = []
    for course in catalog()["courses"]:
        for entry in course["lessons"]:
            lesson = lesson_payload(course["code"], entry["id"])
            if lesson:
                lessons.append({
                    "course": course["code"], "id": entry["id"],
                    "title": lesson["title"], "video_url": lesson["video_url"],
                    "transcript_url": lesson["transcript_url"],
                })
    return {"lessons": lessons}


def page_annotations(note_path: Path) -> dict:
    if not note_path.is_file():
        return {}
    result = {}
    pattern = re.compile(r"^\d+\. \[([^\]]+)\]\((page_\d+\.jpg)\)：(.+)$")
    for line in note_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            result[match.group(2)] = {"heading": match.group(1), "summary": match.group(3)}
    return result


def lesson_payload(code: str, slug: str) -> dict | None:
    if code not in COURSES or not re.fullmatch(r"第[0-9]+周_周[一二三四五六日]_[0-9]+-[0-9]+_[0-9]{4}-[0-9]{2}-[0-9]{2}", slug):
        return None
    manifest = json.loads((ROOT / code / "manifest.json").read_text(encoding="utf-8"))
    resource = next((item for item in manifest.get("resources", [])
                     if item.get("seat") == "屏幕画面" and session_slug(item["session"]) == slug), None)
    if not resource:
        return None
    base = ROOT / code / "notes" / slug
    index_path = safe_file(base / "pages.json")
    if not index_path:
        return None
    data = json.loads(index_path.read_text(encoding="utf-8"))
    annotations = page_annotations(base / "课堂笔记.md")
    details_path = base / "page_details.json"
    details = json.loads(details_path.read_text(encoding="utf-8")) if details_path.is_file() else {}
    pages = []
    for page in data.get("pages", []):
        image = page["image"]
        annotation = annotations.get(image)
        if not annotation:
            continue  # desktop, duplicate animation, or other material rejected during review
        pages.append({
            "number": page["number"], "start": page["start"], "end": page["end"],
            "image_url": file_url(base / image), "heading": annotation["heading"],
            "summary": annotation["summary"],
            "detail": details.get(image),
        })
    transcript = resource.get("transcripts", {}).get("zh", {})
    return {
        "course": code, "lesson": slug, "title": f"{code} · {slug}",
        "video_url": file_url(resource.get("video", {}).get("path")),
        "transcript_url": file_url(transcript.get("vtt")),
        "duration": resource.get("video", {}).get("duration"),
        "pages": pages,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ELRCStudy/1.0"

    def do_GET(self) -> None:
        self.route_request(send_body=True)

    def do_HEAD(self) -> None:
        self.route_request(send_body=False)

    def route_request(self, send_body: bool) -> None:
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        if route == "/api/health":
            return self.json_response({"ok": True}, send_body)
        if route == "/api/catalog":
            return self.json_response(catalog(), send_body)
        if route == "/api/lessons":
            return self.json_response(flat_lessons(), send_body)
        if route == "/api/lesson":
            params = parse_qs(parsed.query)
            result = lesson_payload(params.get("course", [""])[0], params.get("lesson", [""])[0])
            if result is None:
                self.send_error(HTTPStatus.NOT_FOUND, "Lesson not found")
            else:
                self.json_response(result, send_body)
            return
        if route in ("/", "/index.html"):
            return self.serve_file(WEB / "index.html", send_body)
        if route.startswith("/assets/"):
            return self.serve_file(WEB / route[len("/assets/"):], send_body, within=WEB)
        if route.startswith("/media/"):
            return self.serve_file(ROOT / route[len("/media/"):], send_body, within=ROOT)
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def json_response(self, value: dict, send_body: bool) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(payload)

    def serve_file(self, path: Path, send_body: bool, within: Path = WEB) -> None:
        candidate = safe_file(path)
        if not candidate or not candidate.is_relative_to(within.resolve()):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        size = candidate.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get("Range")
        if header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
            if not match or not any(match.groups()):
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "Invalid range")
                return
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
            else:
                start = max(0, size - int(match.group(2)))
            if start >= size or start > end:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, "Outside file")
                return
            end = min(end, size - 1)
        status = HTTPStatus.PARTIAL_CONTENT if header else HTTPStatus.OK
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix in (".js", ".css", ".html", ".json", ".vtt"):
            mime += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store" if candidate.suffix in (".html", ".js", ".css", ".json") else "public, max-age=3600")
        if header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body:
            return
        try:
            with candidate.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ELRC 学习服务: http://{args.host}:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
