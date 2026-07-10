# -*- coding: utf-8 -*-
from typing import List

from pydantic import BaseModel


class CreatorCandidateInput(BaseModel):
    index: int
    nickname: str = ""
    blogger_id: str = ""
    profile_url: str = ""
    price: str = ""


class CreatorScreeningImportRequest(BaseModel):
    filename: str
    content_base64: str


class CreatorScreeningStartRequest(BaseModel):
    requirement: str
    candidates: List[CreatorCandidateInput]
