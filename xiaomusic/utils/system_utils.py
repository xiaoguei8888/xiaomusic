#!/usr/bin/env python3
"""系统操作和环境相关工具函数"""

import copy
import hashlib
import logging
import random
import string
import urllib.parse
from http.cookies import SimpleCookie
from urllib.parse import urlparse

from requests.utils import cookiejar_from_dict

log = logging.getLogger(__package__)


def parse_cookie_string_to_dict(cookie_string: str):
    """
    解析 Cookie 字符串
    Args:
        cookie_string: Cookie 字符串
    Returns:
        CookieJar 对象
    """
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    cookies_dict = {k: m.value for k, m in cookie.items()}
    return cookies_dict


def parse_cookie_string(cookie_string: str):
    """
    解析 Cookie 字符串

    Args:
        cookie_string: Cookie 字符串

    Returns:
        CookieJar 对象
    """
    cookies_dict = parse_cookie_string_to_dict(cookie_string)
    return cookiejar_from_dict(cookies_dict, cookiejar=None, overwrite=True)


def validate_proxy(proxy_str: str) -> bool:
    """
    验证代理字符串格式

    Args:
        proxy_str: 代理字符串

    Returns:
        True 如果格式正确

    Raises:
        ValueError: 如果格式不正确
    """
    parsed = urlparse(proxy_str)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Proxy scheme must be http or https")
    if not (parsed.hostname and parsed.port):
        raise ValueError("Proxy hostname and port must be set")

    return True


def get_random(length: int) -> str:
    """
    生成随机字符串

    Args:
        length: 字符串长度

    Returns:
        随机字符串
    """
    return "".join(random.sample(string.ascii_letters + string.digits, length))


def deepcopy_data_no_sensitive_info(data, fields_to_anonymize: list = None):
    """
    深拷贝数据并脱敏

    Args:
        data: 要拷贝的数据（字典或对象）
        fields_to_anonymize: 需要脱敏的字段列表

    Returns:
        脱敏后的深拷贝数据
    """
    if fields_to_anonymize is None:
        fields_to_anonymize = [
            "account",
            "password",
            "httpauth_username",
            "httpauth_password",
        ]

    copy_data = copy.deepcopy(data)

    # 检查copy_data是否是字典或具有属性的对象
    if isinstance(copy_data, dict):
        # 对字典进行处理
        for field in fields_to_anonymize:
            if field in copy_data:
                copy_data[field] = "******"
    else:
        # 对对象进行处理
        for field in fields_to_anonymize:
            if hasattr(copy_data, field):
                setattr(copy_data, field, "******")

    return copy_data


def try_add_access_control_param(config, url: str) -> str:
    """
    为 URL 添加访问控制参数

    Args:
        config: 配置对象
        url: 原始 URL

    Returns:
        添加了访问控制参数的 URL
    """
    if config.disable_httpauth:
        return url

    url_parts = urllib.parse.urlparse(url)
    file_path = urllib.parse.unquote(url_parts.path)
    correct_code = hashlib.sha256(
        (file_path + config.httpauth_username + config.httpauth_password).encode(
            "utf-8"
        )
    ).hexdigest()
    log.debug(f"rewrite url: [{file_path}, {correct_code}]")

    # make new url
    parsed_get_args = dict(urllib.parse.parse_qsl(url_parts.query))
    parsed_get_args.update({"code": correct_code})
    encoded_get_args = urllib.parse.urlencode(parsed_get_args, doseq=True)
    new_url = urllib.parse.ParseResult(
        url_parts.scheme,
        url_parts.netloc,
        url_parts.path,
        url_parts.params,
        encoded_get_args,
        url_parts.fragment,
    ).geturl()

    return new_url
