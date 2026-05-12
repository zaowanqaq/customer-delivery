# -*- coding: utf-8 -*-
from .crawler import router as crawler_router
from .data import router as data_router
from .websocket import router as websocket_router

__all__ = ["crawler_router", "data_router", "websocket_router"]
