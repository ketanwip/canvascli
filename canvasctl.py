#!/usr/bin/env python3
"""Download Fulton Canvas module materials using a browser session cookie.

This tool is intentionally read-only. It never accepts a cookie on the command
line and never writes authentication data to disk.
"""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


BASE_URL = "https://fultonschools.instructure.com"
COURSES = {
    "math": ("Maths", 547327),
    "science": ("Science", 544570),
    "social": ("SocialStudy", 546905),
}
SUBJECT_ALIASES = {
    "math": "math",
    "maths": "math",
    "science": "science",
    "social": "social",
    "socialstudy": "social",
    "socialstudies": "social",
    "social-studies": "social",
}
DEFAULT_OUTPUT = Path.home() / "Downloads" / "Grade7-Canvas"
USER_AGENT = "fulton-canvasctl/1.0 (local read-only downloader)"
FILE_EXTENSIONS = {
    ".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx",
    ".csv", ".txt", ".rtf", ".zip", ".jpg", ".jpeg", ".png",
    ".gif", ".svg", ".mp3", ".mp4", ".mov", ".m4a",
}


class CanvasError(RuntimeError):
    """Base error with messages safe to display."""


class AuthenticationError(CanvasError):
    """The supplied session cookie is missing, expired, or rejected."""


def eprint(*values: object) -> None:
    print(*values, file=sys.stderr)


def sanitize_filename(value: str, fallback: str = "download") -> str:
    value = urllib.parse.unquote(value or "")
    value = value.replace("\x00", "").replace("/", "_").replace("\\", "_")
    value = re.sub(r"[\r\n\t]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = re.sub(r"[^\w .()\[\]{}+,&'@!#%=-]", "_", value, flags=re.UNICODE)
    return value[:180] or fallback


def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    counter = 2
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def parse_link_header(header: str) -> dict[str, str]:
    links: dict[str, str] = {}
    for part in (header or "").split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"', part)
        if match:
            links[match.group(2)] = match.group(1)
    return links


def content_disposition_filename(value: str) -> Optional[str]:
    if not value:
        return None
    message = Message()
    message["content-disposition"] = value
    filename = message.get_filename()
    return sanitize_filename(filename) if filename else None


class FileLinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() not in {"a", "source", "video", "audio"}:
            return
        values = dict(attrs)
        value = values.get("href") or values.get("src")
        if value:
            self.links.append(value)


def looks_like_file_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    if "/files/" in path or "/api/v1/files/" in path:
        return True
    return Path(path).suffix in FILE_EXTENSIONS


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevent Canvas cookies from crossing origins during redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        old_host = urllib.parse.urlparse(req.full_url).hostname
        new_host = urllib.parse.urlparse(newurl).hostname
        if old_host != new_host:
            for header in ("Cookie", "Authorization", "Referer", "X-requested-with"):
                redirected.remove_header(header)
        return redirected


@dataclass
class ResponseData:
    data: bytes
    final_url: str
    headers: Any


class CanvasClient:
    def __init__(self, base_url: str, cookie: str, timeout: int = 45) -> None:
        self.base_url = base_url.rstrip("/")
        self.base_host = urllib.parse.urlparse(self.base_url).hostname
        self.cookie = validate_cookie(cookie)
        self.timeout = timeout
        self.opener = urllib.request.build_opener(SafeRedirectHandler())

    def _is_canvas_origin(self, url: str) -> bool:
        return urllib.parse.urlparse(url).hostname == self.base_host

    def request(self, url_or_path: str, *, accept: str = "application/json") -> ResponseData:
        url = urllib.parse.urljoin(self.base_url + "/", url_or_path)
        headers = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
        }
        if self._is_canvas_origin(url):
            headers["Cookie"] = self.cookie
            headers["Referer"] = self.base_url + "/"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read()
                final_url = response.geturl()
                response_headers = response.headers
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise AuthenticationError(
                    f"Canvas rejected the session (HTTP {exc.code}). Refresh CANVAS_COOKIE."
                ) from None
            raise CanvasError(f"Canvas request failed (HTTP {exc.code}).") from None
        except urllib.error.URLError as exc:
            reason = type(exc.reason).__name__
            raise CanvasError(f"Network request failed ({reason}).") from None

        final_host = urllib.parse.urlparse(final_url).hostname
        final_path = urllib.parse.urlparse(final_url).path.lower()
        content_type = response_headers.get_content_type()
        if (
            final_host not in {self.base_host, None}
            and content_type == "text/html"
        ) or "login" in final_path:
            raise AuthenticationError(
                "Canvas redirected to a login page. The browser session cookie is expired or incomplete."
            )
        return ResponseData(data=data, final_url=final_url, headers=response_headers)

    def json(self, url_or_path: str) -> Any:
        response = self.request(url_or_path)
        content_type = response.headers.get_content_type()
        if content_type not in {"application/json", "text/json"}:
            sample = response.data[:200].lstrip().lower()
            if sample.startswith(b"<!doctype html") or sample.startswith(b"<html"):
                raise AuthenticationError(
                    "Canvas returned HTML instead of API data. Refresh CANVAS_COOKIE."
                )
        try:
            return json.loads(response.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CanvasError("Canvas returned an unexpected non-JSON response.") from None

    def paginated(self, path: str) -> Iterator[dict[str, Any]]:
        next_url: Optional[str] = path
        pages = 0
        while next_url:
            pages += 1
            if pages > 100:
                raise CanvasError("Canvas pagination exceeded the safety limit.")
            response = self.request(next_url)
            try:
                payload = json.loads(response.data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise CanvasError("Canvas returned invalid paginated API data.") from None
            if not isinstance(payload, list):
                raise CanvasError("Canvas returned an unexpected paginated response.")
            for item in payload:
                if isinstance(item, dict):
                    yield item
            next_url = parse_link_header(response.headers.get("Link", "")).get("next")


def validate_cookie(cookie: str) -> str:
    cookie = (cookie or "").strip()
    if not cookie:
        raise AuthenticationError(
            "CANVAS_COOKIE is not set. Use a hidden shell prompt as shown in README.md."
        )
    if "\r" in cookie or "\n" in cookie:
        raise AuthenticationError("CANVAS_COOKIE contains an invalid newline.")
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if "=" not in cookie:
        raise AuthenticationError(
            "CANVAS_COOKIE must contain the complete Cookie header value (name=value; ...)."
        )
    return cookie


def normalize_subjects(raw: str) -> list[str]:
    if raw.strip().lower() == "all":
        return list(COURSES)
    subjects: list[str] = []
    for value in raw.split(","):
        key = re.sub(r"[ _]", "", value.strip().lower())
        normalized = SUBJECT_ALIASES.get(key)
        if not normalized:
            raise CanvasError(f"Unknown subject: {value.strip()}")
        if normalized not in subjects:
            subjects.append(normalized)
    return subjects


def get_modules(client: CanvasClient, course_id: int) -> list[dict[str, Any]]:
    path = f"/api/v1/courses/{course_id}/modules?per_page=100"
    return list(client.paginated(path))


def select_modules(
    modules: list[dict[str, Any]],
    *,
    query: Optional[str],
    latest: int,
    all_modules: bool,
) -> list[dict[str, Any]]:
    visible = [module for module in modules if not module.get("published") is False]
    if all_modules:
        return sorted(visible, key=lambda m: (int(m.get("position") or 0), int(m.get("id") or 0)))
    if query:
        needle = query.casefold()
        matches = [m for m in visible if needle in str(m.get("name", "")).casefold()]
        if not matches:
            raise CanvasError(f"No published module title contains: {query}")
        return matches
    ordered = sorted(
        visible,
        key=lambda m: (int(m.get("position") or 0), int(m.get("id") or 0)),
        reverse=True,
    )
    return list(reversed(ordered[:latest]))


def get_module_items(client: CanvasClient, course_id: int, module_id: int) -> list[dict[str, Any]]:
    path = (
        f"/api/v1/courses/{course_id}/modules/{module_id}/items"
        "?include%5B%5D=content_details&per_page=100"
    )
    return list(client.paginated(path))


def file_record_from_metadata(metadata: dict[str, Any], source: str) -> Optional[dict[str, Any]]:
    url = metadata.get("url") or metadata.get("download_url")
    if not isinstance(url, str) or not url:
        return None
    filename = metadata.get("display_name") or metadata.get("filename") or metadata.get("name")
    return {
        "url": url,
        "filename": sanitize_filename(str(filename or "download")),
        "file_id": metadata.get("id"),
        "source": source,
    }


def extract_page_file_urls(body: str, base_url: str) -> list[str]:
    parser = FileLinkParser()
    parser.feed(body or "")
    results: list[str] = []
    seen: set[str] = set()
    for link in parser.links:
        absolute = urllib.parse.urljoin(base_url + "/", link)
        if looks_like_file_url(absolute) and absolute not in seen:
            seen.add(absolute)
            results.append(absolute)
    return results


def discover_item_files(
    client: CanvasClient,
    course_id: int,
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    item_type = str(item.get("type") or "")
    title = str(item.get("title") or item_type or "Module item")
    results: list[dict[str, Any]] = []

    if item_type == "File":
        api_url = item.get("url")
        if api_url:
            metadata = client.json(str(api_url))
            if isinstance(metadata, dict):
                record = file_record_from_metadata(metadata, title)
                if record:
                    results.append(record)
        elif item.get("content_id"):
            metadata = client.json(f"/api/v1/files/{item['content_id']}")
            if isinstance(metadata, dict):
                record = file_record_from_metadata(metadata, title)
                if record:
                    results.append(record)

    elif item_type == "Page" and item.get("page_url"):
        page_url = urllib.parse.quote(str(item["page_url"]), safe="")
        page = client.json(f"/api/v1/courses/{course_id}/pages/{page_url}")
        if isinstance(page, dict):
            for url in extract_page_file_urls(str(page.get("body") or ""), client.base_url):
                results.append({"url": url, "filename": "download", "file_id": None, "source": title})

    elif item_type == "Assignment" and item.get("content_id"):
        assignment = client.json(
            f"/api/v1/courses/{course_id}/assignments/{item['content_id']}"
        )
        if isinstance(assignment, dict):
            for attachment in assignment.get("attachments") or []:
                if isinstance(attachment, dict):
                    record = file_record_from_metadata(attachment, title)
                    if record:
                        results.append(record)
            for url in extract_page_file_urls(
                str(assignment.get("description") or ""), client.base_url
            ):
                results.append({"url": url, "filename": "download", "file_id": None, "source": title})

    elif item_type == "ExternalUrl" and item.get("external_url"):
        external_url = str(item["external_url"])
        if looks_like_file_url(external_url):
            results.append(
                {"url": external_url, "filename": "download", "file_id": None, "source": title}
            )

    return deduplicate_records(results)


def deduplicate_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[Any, str]] = set()
    for record in records:
        parsed = urllib.parse.urlsplit(str(record.get("url") or ""))
        safe_url_key = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        key = (record.get("file_id"), safe_url_key)
        if key not in seen:
            seen.add(key)
            output.append(record)
    return output


def infer_filename(record: dict[str, Any], response: ResponseData) -> str:
    disposition = content_disposition_filename(response.headers.get("Content-Disposition", ""))
    if disposition:
        return disposition
    requested = str(record.get("filename") or "")
    if requested and requested != "download" and Path(requested).suffix:
        return sanitize_filename(requested)
    url_name = Path(urllib.parse.urlparse(response.final_url).path).name
    if url_name and url_name.lower() not in {"download", "files"}:
        return sanitize_filename(url_name)
    content_type = response.headers.get_content_type()
    extension = mimetypes.guess_extension(content_type) or ""
    source = sanitize_filename(str(record.get("source") or "download"))
    return source + extension


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_record(
    client: CanvasClient,
    record: dict[str, Any],
    directory: Path,
) -> dict[str, Any]:
    response = client.request(str(record["url"]), accept="*/*")
    filename = infer_filename(record, response)
    directory.mkdir(parents=True, exist_ok=True)
    path = unique_path(directory, filename)
    path.write_bytes(response.data)
    return {
        "filename": path.name,
        "bytes": len(response.data),
        "sha256": sha256_bytes(response.data),
        "file_id": record.get("file_id"),
        "source_item": record.get("source"),
    }


def command_check(client: CanvasClient) -> int:
    profile = client.json("/api/v1/users/self/profile")
    if not isinstance(profile, dict) or not profile.get("id"):
        raise AuthenticationError("Canvas did not return an authenticated user profile.")
    print("Canvas session is valid.")
    print(f"User: {profile.get('name') or profile.get('short_name') or profile['id']}")
    return 0


def command_list(client: CanvasClient, subjects: list[str]) -> int:
    for subject in subjects:
        folder, course_id = COURSES[subject]
        print(f"\n{folder} (course {course_id})")
        modules = get_modules(client, course_id)
        if not modules:
            print("  No modules found.")
            continue
        for module in sorted(modules, key=lambda m: int(m.get("position") or 0)):
            state = "published" if module.get("published") is not False else "unpublished"
            print(
                f"  [{module.get('id')}] {module.get('name', 'Untitled')} "
                f"({module.get('items_count', '?')} items, {state})"
            )
    return 0


def command_download(client: CanvasClient, args: argparse.Namespace) -> int:
    output_root = Path(args.output).expanduser().resolve()
    manifest: dict[str, Any] = {
        "canvas_base_url": client.base_url,
        "subjects": {},
    }
    total_files = 0

    for subject in args.subjects:
        folder, course_id = COURSES[subject]
        modules = get_modules(client, course_id)
        selected = select_modules(
            modules,
            query=args.module_query,
            latest=args.latest,
            all_modules=args.all_modules,
        )
        if not selected:
            eprint(f"{folder}: no published modules selected.")
            manifest["subjects"][folder] = []
            continue

        print(f"\n{folder}: {len(selected)} module(s)")
        subject_manifest: list[dict[str, Any]] = []
        for module in selected:
            module_name = sanitize_filename(str(module.get("name") or f"Module {module['id']}"))
            print(f"  Module: {module_name}")
            module_manifest = {
                "module_id": module.get("id"),
                "module_name": module.get("name"),
                "files": [],
            }
            items = get_module_items(client, course_id, int(module["id"]))
            records: list[dict[str, Any]] = []
            for item in items:
                try:
                    records.extend(discover_item_files(client, course_id, item))
                except CanvasError as exc:
                    eprint(f"    Warning: skipped {item.get('title', 'item')}: {exc}")
            records = deduplicate_records(records)
            if not records:
                print("    No downloadable files discovered.")
            for record in records:
                label = sanitize_filename(str(record.get("filename") or record.get("source") or "file"))
                if args.dry_run:
                    print(f"    Would download: {label}")
                    module_manifest["files"].append(
                        {"filename": label, "file_id": record.get("file_id"), "dry_run": True}
                    )
                else:
                    destination = output_root / folder / module_name
                    try:
                        saved = download_record(client, record, destination)
                    except CanvasError as exc:
                        eprint(f"    Warning: failed {label}: {exc}")
                        continue
                    print(f"    Downloaded: {saved['filename']}")
                    module_manifest["files"].append(saved)
                    total_files += 1
            subject_manifest.append(module_manifest)
        manifest["subjects"][folder] = subject_manifest

    if not args.dry_run:
        output_root.mkdir(parents=True, exist_ok=True)
        manifest_path = output_root / "canvas-download-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\nSaved {total_files} file(s) under: {output_root}")
        print(f"Manifest: {manifest_path}")
    else:
        print("\nDry run complete; nothing was written.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="canvasctl",
        description="Read-only Fulton Canvas module-material downloader.",
    )
    parser.add_argument(
        "--base-url",
        default=BASE_URL,
        help=f"Canvas origin (default: {BASE_URL})",
    )
    parser.add_argument(
        "--cookie-env",
        default="CANVAS_COOKIE",
        help="Environment variable containing the Cookie header (default: CANVAS_COOKIE)",
    )
    parser.add_argument("--timeout", type=int, default=45, help="Request timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("check", help="Verify that the Canvas session is valid")

    list_parser = subparsers.add_parser("list-modules", help="List available course modules")
    list_parser.add_argument("--subject", default="all", help="math, science, social, or all")

    download_parser = subparsers.add_parser("download", help="Download module materials")
    download_parser.add_argument("--subject", default="all", help="math, science, social, or all")
    selection = download_parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--module-query",
        help="Select every published module whose title contains this text",
    )
    selection.add_argument(
        "--all-modules",
        action="store_true",
        help="Download from all published modules",
    )
    download_parser.add_argument(
        "--latest",
        type=int,
        default=1,
        help="Number of latest positioned modules per course (default: 1)",
    )
    download_parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Download root (default: {DEFAULT_OUTPUT})",
    )
    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover and display files without downloading",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout < 1 or args.timeout > 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    cookie = os.environ.get(args.cookie_env, "")
    try:
        client = CanvasClient(args.base_url, cookie, timeout=args.timeout)
        if args.command == "check":
            return command_check(client)
        if args.command == "list-modules":
            return command_list(client, normalize_subjects(args.subject))
        if args.command == "download":
            if args.latest < 1:
                parser.error("--latest must be at least 1")
            args.subjects = normalize_subjects(args.subject)
            return command_download(client, args)
        parser.error("unknown command")
    except AuthenticationError as exc:
        eprint(f"Authentication error: {exc}")
        return 3
    except CanvasError as exc:
        eprint(f"Error: {exc}")
        return 2
    except KeyboardInterrupt:
        eprint("Cancelled.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
