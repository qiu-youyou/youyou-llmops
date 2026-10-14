"""聊天入口共享的输入字段；业务层直接接收具体请求 Schema。"""
import ipaddress
import re
from urllib.parse import urlsplit

from flask import request
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import ValidationError

from internal.schema.schema import ListField

MAX_MESSAGE_IMAGES = 5
MAX_IMAGE_URL_LENGTH = 4096


class ChatReq(FlaskForm):
    query = StringField("query", default="")
    image_urls = ListField("image_urls", default=list)

    def validate_query(self, field: StringField) -> None:
        # StringField 会把 JSON 数字转成字符串，校验时需要检查原始类型。
        query = request.get_json().get("query", "") if request.is_json else field.data
        if not isinstance(query, str):
            raise ValidationError("query 必须为字符串")
        if not query.strip() and not self.image_urls.data:
            raise ValidationError("文字与图片不能同时为空")

    def validate_image_urls(self, field: ListField) -> None:
        # JSON 经 MultiDict 后，单个字符串与单元素数组都会变为 list。
        # 检查原始 JSON 才能拒绝 image_urls: "https://..." 这类错误请求。
        image_urls = field.data
        if request.is_json:
            image_urls = request.get_json().get("image_urls", [])
        if not isinstance(image_urls, list):
            raise ValidationError("image_urls 必须为数组")
        if len(image_urls) > MAX_MESSAGE_IMAGES:
            raise ValidationError(f"每条消息最多{MAX_MESSAGE_IMAGES}张图片")

        for image_url in image_urls:
            self._validate_image_url(image_url)

    @staticmethod
    def _validate_image_url(image_url: str) -> None:
        """只校验地址格式，不请求图片或执行 DNS 查询。"""
        if (
            not isinstance(image_url, str)
            or not image_url
            or len(image_url) > MAX_IMAGE_URL_LENGTH
        ):
            raise ValidationError(f"图片地址必须为字符串，长度不能超过{MAX_IMAGE_URL_LENGTH}")
        if "\\" in image_url or any(char.isspace() or ord(char) < 32 for char in image_url):
            raise ValidationError("图片 URL 格式不正确")

        try:
            url = urlsplit(image_url)
            host = (url.hostname or "").lower().rstrip(".")
            _ = url.port  # 触发 urllib 对端口格式和范围的校验。
            if (
                url.scheme != "https"
                or not host
                or url.username is not None
                or url.password is not None
            ):
                raise ValueError("需要不含账号密码的 HTTPS 地址")
            if host == "localhost" or host.endswith(".localhost"):
                raise ValueError("不允许本机地址")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                labels = host.encode("idna").decode("ascii").split(".")
                valid_labels = all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels
                )
                if len(labels) < 2 or len(host) > 253 or not valid_labels:
                    raise ValueError("域名格式不正确")
                if all(
                    re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label)
                    for label in labels
                ):
                    raise ValueError("不允许非标准 IP 表示")
            else:
                if not address.is_global:
                    raise ValueError("不允许内网地址")
        except (ValueError, UnicodeError) as exc:
            raise ValidationError("图片地址必须是公网 HTTPS URL，不允许账号密码或内网地址") from exc
