# -*- coding: utf-8 -*-
import pytest

from api.services.creator_screening import parse_creator_screening_file


def test_creator_screening_import_requires_all_customer_columns():
    content = "达人昵称,主页链接\n甲,https://www.xiaohongshu.com/user/profile/a\n".encode("utf-8")

    with pytest.raises(ValueError, match="缺少必需列"):
        parse_creator_screening_file("creators.csv", content)


def test_creator_screening_import_requires_profile_url_and_reports_invalid_or_duplicate_rows():
    content = (
        "达人昵称,博主ID,主页链接,达人价格\n"
        "甲,creator_1,https://www.xiaohongshu.com/user/profile/a,1000\n"
        "乙,,,2000\n"
        "丙,creator_1,,3000\n"
        "丁,,https://www.xiaohongshu.com/user/profile/d,4000\n"
    ).encode("utf-8")

    result = parse_creator_screening_file("creators.csv", content)

    assert [(item.index, item.nickname, item.blogger_id, item.price) for item in result.candidates] == [
        (1, "甲", "creator_1", "1000"),
        (2, "丁", "", "4000"),
    ]
    assert result.invalid_rows == [
        {"row": 3, "reason": "主页链接必填"},
        {"row": 4, "reason": "主页链接必填"},
    ]
