# -*- coding: utf-8 -*-
from api.main import OPS_CONFIG_DEFAULT, PROJECT_BOUND_FIELDS, OpsConfigPayload


def test_ops_config_preserves_collaboration_comment_table_fields():
    payload = OpsConfigPayload(
        platform="xhs",
        collab_comments_table_name="合作笔记评论表",
        collab_comments_table_id="tbl_collab_comments",
    )
    data = payload.model_dump()

    assert OPS_CONFIG_DEFAULT["collab_comments_table_name"] == "合作笔记评论表"
    assert OPS_CONFIG_DEFAULT["collab_comments_table_id"] == ""
    assert data["collab_comments_table_name"] == "合作笔记评论表"
    assert data["collab_comments_table_id"] == "tbl_collab_comments"
    assert "collab_comments_table_id" in PROJECT_BOUND_FIELDS
