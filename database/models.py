# -*- coding: utf-8 -*-
from sqlalchemy import BigInteger, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class XhsCreator(Base):
    __tablename__ = "xhs_creator"

    id = Column(Integer, primary_key=True, comment="主键ID")
    user_id = Column(String(255), comment="用户ID")
    nickname = Column(Text, comment="用户昵称")
    avatar = Column(Text, comment="用户头像")
    ip_location = Column(Text, comment="IP地址位置")
    add_ts = Column(BigInteger, comment="添加时间戳")
    last_modify_ts = Column(BigInteger, comment="最后修改时间戳")
    desc = Column(Text, comment="描述")
    gender = Column(Text, comment="性别")
    follows = Column(Text, comment="关注数")
    fans = Column(Text, comment="粉丝数")
    interaction = Column(Text, comment="互动数")
    tag_list = Column(Text, comment="标签列表")


class XhsNote(Base):
    __tablename__ = "xhs_note"

    id = Column(Integer, primary_key=True, comment="主键ID")
    user_id = Column(String(255), comment="用户ID")
    nickname = Column(Text, comment="用户昵称")
    avatar = Column(Text, comment="用户头像")
    ip_location = Column(Text, comment="IP地址位置")
    add_ts = Column(BigInteger, comment="添加时间戳")
    last_modify_ts = Column(BigInteger, comment="最后修改时间戳")
    note_id = Column(String(255), index=True, comment="笔记ID")
    type = Column(Text, comment="笔记类型")
    title = Column(Text, comment="笔记标题")
    desc = Column(Text, comment="笔记描述")
    video_url = Column(Text, comment="视频URL")
    time = Column(BigInteger, index=True, comment="时间戳")
    last_update_time = Column(BigInteger, comment="最后更新时间戳")
    liked_count = Column(Text, comment="点赞数")
    collected_count = Column(Text, comment="收藏数")
    comment_count = Column(Text, comment="评论数")
    share_count = Column(Text, comment="分享数")
    image_list = Column(Text, comment="图片列表")
    tag_list = Column(Text, comment="标签列表")
    note_url = Column(Text, comment="笔记URL")
    source_keyword = Column(Text, default="", comment="来源关键词")
    xsec_token = Column(Text, comment="Xsec Token")


class XhsNoteComment(Base):
    __tablename__ = "xhs_note_comment"

    id = Column(Integer, primary_key=True, comment="主键ID")
    user_id = Column(String(255), comment="用户ID")
    nickname = Column(Text, comment="用户昵称")
    avatar = Column(Text, comment="用户头像")
    ip_location = Column(Text, comment="IP地址位置")
    add_ts = Column(BigInteger, comment="添加时间戳")
    last_modify_ts = Column(BigInteger, comment="最后修改时间戳")
    comment_id = Column(String(255), index=True, comment="评论ID")
    create_time = Column(BigInteger, index=True, comment="创建时间戳")
    note_id = Column(String(255), comment="笔记ID")
    content = Column(Text, comment="评论内容")
    sub_comment_count = Column(Integer, comment="子评论数")
    pictures = Column(Text, comment="图片")
    parent_comment_id = Column(String(255), comment="父评论ID")
    like_count = Column(Text, comment="点赞数")
