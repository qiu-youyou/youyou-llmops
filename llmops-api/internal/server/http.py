#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@File   :   http
@Time   :   2025/9/1 13:54
@Author :   s.qiu@foxmail.com
"""
import os

from flask import Flask
from flask_cors import CORS
from flask_login import LoginManager
from flask_migrate import Migrate

from config import Config
from internal.exception import CustomException
from internal.extension import celery_extension
from internal.extension import logging_extension
from internal.extension import redis_extension
from internal.extension.weaviate_extension import weaviate
from internal.middleware import Middleware
from internal.router import Router
from pkg.response import json, Response, fail_message
from pkg.sqlalchemy import SQLAlchemy


class Http(Flask):
    """HTTP 服务引擎"""

    def __init__(
            self,
            *args,
            conf: Config,
            router: Router,
            db: SQLAlchemy,
            migrate: Migrate,
            login_manager: LoginManager,
            middleware: Middleware,
            **kwargs):

        super().__init__(*args, **kwargs)

        # 初始化配置
        self.config.from_object(conf)

        # JSON 响应直接输出中文，便于查看和调试
        self.json.ensure_ascii = False

        # 自定义异常错误
        self.register_error_handler(Exception, self._register_error_handler)

        # 初始化扩展
        db.init_app(self)
        migrate.init_app(self, db, "internal/migrations")
        login_manager.init_app(self)
        redis_extension.init_app(self)
        celery_extension.init_app(self)
        logging_extension.init_app(self)
        weaviate.init_app(self)

        # 注册中间件
        login_manager.request_loader(middleware.request_loader)

        # 处理跨域
        CORS(self, resources={
            r"/*": {
                "origins": "*",
                "supports_credentials": True,
                # "methods": ["GET", "POST"],
                # "allow_headers": ["Content-Type"],
            }
        })

        # 注册路由
        router.register_router(self)

    def _register_error_handler(self, error: Exception):
        # 是否为抛出的 自定义异常
        if isinstance(error, CustomException):
            return json(Response(
                code=error.code,
                message=error.message,
                data=error.data if error.data is not None else {},
            ))

        # 如果开发环境抛出异常更详细的信息
        if self.debug or os.getenv("FLASK_ENV") == "development":
            raise error

        # 生产环境返回 FAIL
        else:
            # 不把数据库语句、连接信息或上游异常中的凭证回传客户端。
            self.logger.error("Unhandled request error: %s", type(error).__name__)
            return fail_message("服务暂时不可用，请稍后重试")
