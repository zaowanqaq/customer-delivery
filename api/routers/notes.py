# -*- coding: utf-8 -*-
import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/notes", tags=["notes"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "xhs"
IMAGE_DIR = DATA_DIR / "images"
SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
NOTE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")


def _parse_count(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).strip().replace(",", "").replace("+", "")
    if not text:
        return 0
    try:
        lower = text.lower()
        if "亿" in text:
            return int(float(text.replace("亿", "")) * 100_000_000)
        if "万" in text:
            return int(float(text.replace("万", "")) * 10_000)
        if "w" in lower:
            return int(float(lower.replace("w", "")) * 10_000)
        if "k" in lower:
            return int(float(lower.replace("k", "")) * 1_000)
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _safe_note_id(note_id: str) -> str:
    if not NOTE_ID_PATTERN.match(note_id or ""):
        raise HTTPException(status_code=400, detail="Invalid note_id")
    return note_id


def _split_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in text.split(",") if item.strip()]
    return [str(value).strip()]


def _content_files() -> List[Path]:
    files: List[Path] = []
    for folder, patterns in {
        "jsonl": ["*contents*.jsonl"],
        "json": ["*contents*.json"],
        "csv": ["*contents*.csv"],
    }.items():
        dir_path = DATA_DIR / folder
        if not dir_path.exists():
            continue
        for pattern in patterns:
            files.extend(dir_path.glob(pattern))
    return sorted(files, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _read_json(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if isinstance(obj, list):
        return [item for item in obj if isinstance(item, dict)]
    if isinstance(obj, dict):
        return [obj]
    return []


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _image_files(note_id: str) -> List[Path]:
    if not NOTE_ID_PATTERN.match(note_id or ""):
        return []
    note_dir = IMAGE_DIR / note_id
    try:
        note_dir.resolve().relative_to(IMAGE_DIR.resolve())
    except (OSError, ValueError):
        return []
    if not note_dir.exists() or not note_dir.is_dir():
        return []
    return sorted(
        [p for p in note_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTS],
        key=lambda p: p.name,
    )


def _read_notes() -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for path in _content_files():
        try:
            if path.suffix == ".jsonl":
                rows = _read_jsonl(path)
            elif path.suffix == ".json":
                rows = _read_json(path)
            elif path.suffix == ".csv":
                rows = _read_csv(path)
            else:
                rows = []
        except (OSError, json.JSONDecodeError, csv.Error):
            continue

        for row in rows:
            note_id = str(row.get("note_id") or "").strip()
            if not note_id or note_id in by_id:
                continue
            row["_source_file"] = str(path.relative_to(PROJECT_ROOT))
            by_id[note_id] = row

    notes = list(by_id.values())
    notes.sort(key=_sort_time, reverse=True)
    return notes


def _sort_time(item: Dict[str, Any]) -> int:
    raw = item.get("time") or item.get("last_update_time") or 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _format_note(note: Dict[str, Any], include_detail: bool = False) -> Dict[str, Any]:
    note_id = str(note.get("note_id") or "")
    local_images = _image_files(note_id)
    remote_images = _split_list(note.get("image_list"))
    image_urls = [f"/api/notes/{note_id}/images/{p.name}" for p in local_images] or remote_images
    image_source = "local" if local_images else ("remote" if remote_images else "none")
    tag_list = _split_list(note.get("tag_list"))
    video_url = str(note.get("video_url") or "")
    payload: Dict[str, Any] = {
        "note_id": note_id,
        "type": note.get("type") or ("video" if video_url else "normal"),
        "title": note.get("title") or note.get("标题") or "",
        "desc": note.get("desc") or "",
        "nickname": note.get("nickname") or note.get("author_nickname") or "",
        "avatar": note.get("avatar") or "",
        "liked_count": str(note.get("liked_count") or "0"),
        "liked_count_num": _parse_count(note.get("liked_count")),
        "collected_count": str(note.get("collected_count") or "0"),
        "collected_count_num": _parse_count(note.get("collected_count")),
        "comment_count": str(note.get("comment_count") or "0"),
        "comment_count_num": _parse_count(note.get("comment_count")),
        "share_count": str(note.get("share_count") or "0"),
        "share_count_num": _parse_count(note.get("share_count")),
        "source_keyword": note.get("source_keyword") or "",
        "note_url": note.get("note_url") or "",
        "time": note.get("time") or 0,
        "tag_list": tag_list,
        "image_count": len(image_urls),
        "local_image_count": len(local_images),
        "image_source": image_source,
        "first_image_url": image_urls[0] if image_urls else "",
        "video_url": video_url,
        "source_file": note.get("_source_file") or "",
    }
    if include_detail:
        payload["image_urls"] = image_urls
        payload["raw"] = note
    return payload


@router.get("")
async def list_notes(
    keyword: Optional[str] = Query(None, max_length=80),
    search: Optional[str] = Query(None, max_length=120),
    offset: int = Query(0, ge=0),
    limit: int = Query(80, ge=1, le=300),
) -> Dict[str, Any]:
    notes = _read_notes()
    if keyword:
        needle = keyword.lower()
        notes = [item for item in notes if needle in str(item.get("source_keyword") or "").lower()]
    if search:
        needle = search.lower()
        notes = [
            item for item in notes
            if needle in str(item.get("title") or item.get("标题") or "").lower()
            or needle in str(item.get("desc") or "").lower()
        ]
    total = len(notes)
    page = notes[offset:offset + limit]
    return {"notes": [_format_note(item) for item in page], "total": total, "offset": offset, "limit": limit}


@router.get("/stats")
async def get_notes_stats() -> Dict[str, Any]:
    notes = _read_notes()
    keywords: Dict[str, int] = {}
    total_images = 0
    total_likes = 0
    video_count = 0
    for note in notes:
        formatted = _format_note(note)
        total_images += int(formatted["image_count"])
        total_likes += int(formatted["liked_count_num"])
        if formatted["type"] == "video" or formatted["video_url"]:
            video_count += 1
        keyword = formatted["source_keyword"] or "未标注"
        keywords[keyword] = keywords.get(keyword, 0) + 1
    return {
        "total_notes": len(notes),
        "total_images": total_images,
        "total_likes": total_likes,
        "video_count": video_count,
        "keywords_stats": dict(sorted(keywords.items(), key=lambda item: item[1], reverse=True)),
        "recent_notes": [_format_note(item) for item in notes[:6]],
    }


@router.get("/keywords")
async def get_keywords() -> Dict[str, Any]:
    keywords = sorted({str(item.get("source_keyword") or "").strip() for item in _read_notes() if item.get("source_keyword")})
    return {"keywords": keywords}


@router.get("/{note_id}")
async def get_note_detail(note_id: str) -> Dict[str, Any]:
    safe_note_id = _safe_note_id(note_id)
    for note in _read_notes():
        if str(note.get("note_id") or "") == safe_note_id:
            return _format_note(note, include_detail=True)
    raise HTTPException(status_code=404, detail="Note not found")


@router.get("/{note_id}/images/{image_name}")
async def get_note_image(note_id: str, image_name: str) -> FileResponse:
    safe_note_id = _safe_note_id(note_id)
    if "/" in image_name or "\\" in image_name or Path(image_name).suffix.lower() not in SUPPORTED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="Invalid image name")
    image_path = IMAGE_DIR / safe_note_id / image_name
    try:
        image_path.resolve().relative_to(IMAGE_DIR.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=403, detail="Access denied")
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)
