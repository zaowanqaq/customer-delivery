# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from api.schemas.creator_screening import CreatorCandidateInput


REQUIRED_CREATOR_COLUMNS = ("达人昵称", "博主ID", "主页链接", "达人价格")


@dataclass
class CreatorImportResult:
    candidates: List[CreatorCandidateInput] = field(default_factory=list)
    invalid_rows: List[dict] = field(default_factory=list)


def _read_csv_rows(content: bytes) -> List[dict]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        return list(csv.DictReader(io.StringIO(text)))
    raise ValueError("CSV 文件编码无法识别，请使用 UTF-8 或 GB18030") from last_error


def _read_excel_rows(content: bytes) -> List[dict]:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise ValueError(f"读取 Excel 失败：缺少 pandas/openpyxl 依赖。{exc}") from exc
    try:
        frame = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ValueError(f"文件解析失败：{exc}") from exc
    return frame.to_dict(orient="records")


def _clean_cell(value: object) -> str:
    return "" if value is None else str(value).strip()


def parse_creator_screening_file(filename: str, content: bytes) -> CreatorImportResult:
    if not filename:
        raise ValueError("缺少文件名")
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("文件过大，请控制在 10MB 以内")

    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        rows = _read_csv_rows(content)
    elif suffix in {".xlsx", ".xls"}:
        rows = _read_excel_rows(content)
    else:
        raise ValueError("仅支持 csv、xlsx、xls 文件")

    headers = set(rows[0].keys()) if rows else set()
    missing = [name for name in REQUIRED_CREATOR_COLUMNS if name not in headers]
    if missing:
        raise ValueError(f"缺少必需列：{'、'.join(missing)}")

    result = CreatorImportResult()
    seen: dict[str, int] = {}
    for excel_row, row in enumerate(rows, start=2):
        nickname = _clean_cell(row.get("达人昵称"))
        blogger_id = _clean_cell(row.get("博主ID"))
        profile_url = _clean_cell(row.get("主页链接"))
        price = _clean_cell(row.get("达人价格"))
        if not blogger_id and not profile_url:
            result.invalid_rows.append({"row": excel_row, "reason": "博主ID或主页链接至少填写一项"})
            continue
        identity = (profile_url or blogger_id).lower()
        if identity in seen:
            result.invalid_rows.append({"row": excel_row, "reason": f"与第{seen[identity]}行重复"})
            continue
        seen[identity] = excel_row
        result.candidates.append(
            CreatorCandidateInput(
                index=len(result.candidates) + 1,
                nickname=nickname,
                blogger_id=blogger_id,
                profile_url=profile_url,
                price=price,
            )
        )
    return result
