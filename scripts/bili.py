# requires python3.10
# -*- coding: utf-8 -*-
"""Bilibili 数据层：curl_cffi 指纹请求 + Wbi 签名 + .env 配置。

宿主端通过 stdin 传 JSON 参数、stdout 只输出一行最终 JSON：
    {"code": 0, "data": {...}}            成功
    {"code": <错误码>, "message": "..."}   失败
所有诊断日志写 stderr，绝不打印 Cookie 值。

依赖：curl_cffi、python-dotenv（见 scripts/requirements.txt）。
"""

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from functools import reduce
from pathlib import Path
from urllib.parse import urlencode

# 子进程 cwd 不可靠：必须用 __file__ 反推插件根目录的 .env，禁止无参 load_dotenv()。
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PLUGIN_ROOT / ".env"

from dotenv import load_dotenv  # noqa: E402  (先定位 .env，再加载)

load_dotenv(dotenv_path=ENV_PATH)

from curl_cffi import requests as cffi_requests  # noqa: E402

API_ROOT = "https://api.bilibili.com"
HOME_URL = "https://www.bilibili.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
IMPERSONATE_CANDIDATES = ("chrome131", "chrome124", "chrome116")
REQUEST_TIMEOUT = 20
MIN_REQUEST_INTERVAL = 2.0  # 全局 QPS <= 0.5
WBI_TTL_SECONDS = 24 * 60 * 60

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

ERROR_HINTS = {
    412: "TLS 指纹拦截（412）：请确认 curl_cffi 可用，且 impersonate 版本有效",
    -352: "Wbi 签名异常或 Cookie 失效，请检查 .env 中 BILI_COOKIE",
    -799: "请求频率过高（-799），请稍后重试",
    -404: "资源不存在（-404）：请检查 uid/bvid 是否正确",
    -101: "账号未登录（-101）：Cookie 缺少有效的 SESSDATA",
    -400: "请求参数错误（-400）",
    -403: "访问被拒绝（-403）",
}

VALID_ORDERS = ("pubdate", "click", "stow")


class BiliError(Exception):
    """带 B 站错误码的业务异常。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)


def log(message):
    """诊断信息只写 stderr，stdout 只能有最终 JSON。"""
    try:
        sys.stderr.write("[bili] " + str(message) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def mask_cookie(cookie):
    """只暴露 Cookie 的项数与关键键是否存在，绝不暴露值。"""
    parts = [p.strip() for p in cookie.split(";") if p.strip()]
    keys = set()
    for part in parts:
        key = part.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return "%d 项（SESSDATA:%s，buvid3:%s）" % (
        len(parts),
        "有" if "SESSDATA" in keys else "无",
        "有" if "buvid3" in keys else "无",
    )


def read_cookie():
    return (os.environ.get("BILI_COOKIE") or "").strip()


# ---------------------------------------------------------------------------
# 会话与限速
# ---------------------------------------------------------------------------

_session = None
_impersonate = None
_last_request_ts = 0.0


def get_session():
    """全局单例 curl_cffi Session；按 chrome131 -> chrome124 -> chrome116 降级。"""
    global _session, _impersonate
    if _session is not None:
        return _session

    cookie = read_cookie()
    last_error = None
    for name in IMPERSONATE_CANDIDATES:
        try:
            session = cffi_requests.Session(impersonate=name)
        except Exception as exc:  # 当前 curl_cffi 不支持该指纹
            last_error = exc
            log("impersonate %s 不可用：%s" % (name, exc))
            continue
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": HOME_URL,
            "Origin": "https://www.bilibili.com",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cookie": cookie,
        })
        _session = session
        _impersonate = name
        log("curl_cffi 会话就绪：impersonate=%s；Cookie %s" % (name, mask_cookie(cookie)))
        return _session

    raise BiliError(-1, "curl_cffi 无法创建浏览器指纹会话：%s" % (last_error,))


def throttle():
    """保证相邻请求间隔 >= 2 秒。"""
    global _last_request_ts
    wait = MIN_REQUEST_INTERVAL - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def http_get(path, params, headers=None):
    """发一次 GET，返回解析后的外层 JSON；只处理传输层错误，不解释业务 code。"""
    url = API_ROOT + path
    session = get_session()
    throttle()
    try:
        response = session.get(url, params=params, headers=headers or None, timeout=REQUEST_TIMEOUT)
    except Exception as exc:
        raise BiliError(-1, "请求 B 站失败：%s。请检查网络或代理设置。" % (exc,))

    if response.status_code == 412:
        raise BiliError(412, ERROR_HINTS[412])
    if response.status_code != 200:
        raise BiliError(-1, "B 站返回 HTTP %s：%s" % (response.status_code, response.text[:200]))

    try:
        body = response.json()
    except Exception:
        raise BiliError(-1, "B 站返回的不是 JSON：%s" % (response.text[:200],))
    if not isinstance(body, dict):
        raise BiliError(-1, "B 站返回结构异常：%s" % (str(body)[:200],))
    return body


def request_json(path, params, signed=False, allowed_codes=(0,), headers=None, retries=2):
    """带 Wbi 签名（可选）、-799 重试一次的业务请求，返回 data。

    签名接口（空间投稿）对匿名 Cookie 的风控是概率性的：实测同一参数会随机返回
    412 / -352 / 0。因此对 412 与 -352 做有限重试（最多 retries 次，退避 3s/6s），
    并在 -352 时强制刷新一次 Wbi 密钥。重试仍失败才抛错。
    """
    params = dict(params or {})
    wbi_keys = None
    if signed:
        wbi_keys = get_wbi_keys()

    transient_retries = retries if signed else 0
    rate_limited = False
    while True:
        query = dict(params)
        if signed:
            query = enc_wbi(query, wbi_keys[0], wbi_keys[1])

        try:
            body = http_get(path, query, headers=headers)
            code = int(body.get("code", -1))
        except BiliError as exc:
            if exc.code == 412 and transient_retries > 0:
                transient_retries -= 1
                wait_seconds = 3 + (retries - transient_retries) * 3
                log("空间接口返回 412，%d 秒后重试（剩余重试 %d 次）" % (wait_seconds, transient_retries))
                time.sleep(wait_seconds)
                if signed:
                    wbi_keys = get_wbi_keys(force=True)
                continue
            raise

        if code in allowed_codes:
            return body.get("data")

        if code == -799 and not rate_limited:
            rate_limited = True
            log("触发 -799 频率限制，等待 10 秒后重试一次")
            time.sleep(10)
            continue

        if code == -352 and transient_retries > 0:
            transient_retries -= 1
            wait_seconds = 3 + (retries - transient_retries) * 3
            log("空间接口返回 -352，%d 秒后刷新 Wbi 并重试（剩余重试 %d 次）" % (wait_seconds, transient_retries))
            time.sleep(wait_seconds)
            if signed:
                wbi_keys = get_wbi_keys(force=True)
            continue

        message = str(body.get("message") or "").strip()
        hint = ERROR_HINTS.get(code)
        if hint:
            raise BiliError(code, "%s（B 站 message：%s）" % (hint, message or "无"))
        raise BiliError(code, "B 站接口返回错误 code=%s：%s" % (code, message or "无"))


# ---------------------------------------------------------------------------
# Wbi 签名（照抄指南 2.3 参考实现，不自行改写）
# ---------------------------------------------------------------------------

_wbi_keys = None
_wbi_fetched_at = 0.0


def get_mixin_key(raw):
    return reduce(lambda s, i: s + raw[i], MIXIN_KEY_ENC_TAB, "")[:32]


def enc_wbi(params, img_key, sub_key):
    mixin_key = get_mixin_key(img_key + sub_key)
    params = dict(params)
    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    # 过滤 value 中的 "!'()*" 字符
    params = {k: "".join(filter(lambda c: c not in "!'()*", str(v))) for k, v in params.items()}
    query = urlencode(params)
    params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return params


def get_wbi_keys(force=False):
    """进程内缓存 24 小时；nav 未登录（code=-101）时仍带 wbi_img。"""
    global _wbi_keys, _wbi_fetched_at
    now = time.time()
    if not force and _wbi_keys is not None and (now - _wbi_fetched_at) < WBI_TTL_SECONDS:
        return _wbi_keys

    body = http_get("/x/web-interface/nav", {})
    code = int(body.get("code", -1))
    data = body.get("data") or {}
    wbi_img = data.get("wbi_img") or {}
    img_url = wbi_img.get("img_url") or ""
    sub_url = wbi_img.get("sub_url") or ""
    if not img_url or not sub_url:
        hint = ERROR_HINTS.get(code)
        raise BiliError(
            code if code != 0 else -1,
            "%s 无法从 nav 接口取得 Wbi 密钥 img_url/sub_url。" % (hint + "；" if hint else ""),
        )

    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    _wbi_keys = (img_key, sub_key)
    _wbi_fetched_at = now
    log("Wbi 密钥已刷新：img=%s sub=%s（缓存 24h）" % (img_key, sub_key))
    return _wbi_keys


# ---------------------------------------------------------------------------
# 参数校验与小工具
# ---------------------------------------------------------------------------

def require_string(args, key, label):
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BiliError(-1, "%s 不能为空，必须是字符串" % (label,))
    return value.strip()


def normalize_uid(args):
    raw = require_string(args, "uid", "uid")
    if raw.lower().startswith("uid:"):
        raw = raw[4:].strip()
    if not raw.isdigit():
        raise BiliError(-1, "uid 必须是纯数字（例如 2），收到：%s" % (raw,))
    uid = int(raw)
    if uid <= 0:
        raise BiliError(-1, "uid 必须大于 0")
    return uid


def normalize_bvid(args):
    bvid = require_string(args, "bvid", "bvid")
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid):
        raise BiliError(-1, "bvid 格式不正确，应形如 BV1GJ411x7h7")
    return bvid


def normalize_int(args, key, default, minimum, maximum):
    value = args.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BiliError(-1, "%s 必须是整数" % (key,))
    value = int(value)
    if value < minimum or value > maximum:
        raise BiliError(-1, "%s 必须在 %d 到 %d 之间" % (key, minimum, maximum))
    return value


def strip_html(text):
    if not isinstance(text, str):
        return text
    return html.unescape(re.sub(r"<[^>]+>", "", text))


# ---------------------------------------------------------------------------
# 四个 action
# ---------------------------------------------------------------------------

def action_user_stat(args):
    uid = normalize_uid(args)
    stat = request_json("/x/relation/stat", {"vmid": uid})
    result = {
        "uid": uid,
        "mid": stat.get("mid", uid),
        "name": None,
        "follower": stat.get("follower"),
        "following": stat.get("following"),
        "archive_count": None,
    }
    # 账号卡片补昵称与投稿数；卡片接口偶发失败不应让粉丝数查询整体失败。
    try:
        card = request_json("/x/web-interface/card", {"mid": uid})
        card_info = card.get("card") or {}
        result["name"] = strip_html(card_info.get("name"))
        result["archive_count"] = card.get("archive_count")
    except BiliError as exc:
        log("账号卡片接口失败，仅返回关系统计：%s" % (exc.message,))
    return result


def action_user_videos(args):
    uid = normalize_uid(args)
    page = normalize_int(args, "page", 1, 1, 100)
    page_size = normalize_int(args, "ps", 30, 1, 50)
    order = args.get("order") or "pubdate"
    if order not in VALID_ORDERS:
        raise BiliError(-1, "order 只能是 pubdate / click / stow")

    params = {
        "mid": uid,
        "ps": page_size,
        "pn": page,
        "order": order,
        "platform": "web",
        "web_location": 1550101,
        "order_avoided": "true",
    }
    data = request_json(
        "/x/space/wbi/arc/search",
        params,
        signed=True,
        # 空间投稿接口按来源页做风控：用空间页 Referer 比 www 首页稳定（实测 www 会 -352/412）。
        headers={
            "Referer": "https://space.bilibili.com/%d/video" % (uid,),
            "Origin": "https://space.bilibili.com",
        },
    ) or {}
    page_info = data.get("page") or {}
    vlist = (data.get("list") or {}).get("vlist") or []
    items = []
    for video in vlist:
        items.append({
            "bvid": video.get("bvid"),
            "aid": video.get("aid"),
            "title": strip_html(video.get("title")),
            "author": strip_html(video.get("author")),
            "length": video.get("length"),
            "created": video.get("created"),
            "play": video.get("play"),
            "comment": video.get("comment"),
            "danmaku": video.get("video_review"),
        })
    return {
        "mid": uid,
        "pn": page_info.get("pn", page),
        "ps": page_info.get("ps", page_size),
        "order": order,
        "total": page_info.get("count", len(items)),
        "items": items,
    }


def action_video_info(args):
    bvid = normalize_bvid(args)
    data = request_json("/x/web-interface/view", {"bvid": bvid}) or {}
    stat = data.get("stat") or {}
    owner = data.get("owner") or {}
    return {
        "aid": data.get("aid"),
        "bvid": data.get("bvid", bvid),
        "title": data.get("title"),
        "pubdate": data.get("pubdate"),
        "duration": data.get("duration"),
        "owner": {
            "mid": owner.get("mid"),
            "name": strip_html(owner.get("name")),
        },
        "stat": {
            "view": stat.get("view"),
            "like": stat.get("like"),
            "coin": stat.get("coin"),
            "favorite": stat.get("favorite"),
            "share": stat.get("share"),
            "danmaku": stat.get("danmaku"),
            "reply": stat.get("reply"),
        },
    }


def action_search_user(args):
    keyword = require_string(args, "keyword", "keyword")
    if len(keyword) > 100:
        raise BiliError(-1, "keyword 过长（最多 100 字）")
    data = request_json(
        "/x/web-interface/search/type",
        {"search_type": "bili_user", "keyword": keyword, "page": 1},
    ) or {}
    result = data.get("result") or []
    items = []
    for user in result:
        items.append({
            "mid": user.get("mid"),
            "uname": strip_html(user.get("uname")),
            "usign": strip_html(user.get("usign")),
            "fans": user.get("fans"),
            "videos": user.get("videos"),
            "level": user.get("level"),
            "room_id": user.get("room_id"),
        })
    return {
        "keyword": keyword,
        "page": 1,
        "total": data.get("numResults", len(items)),
        "items": items,
    }


ACTIONS = {
    "bili_user_stat": action_user_stat,
    "bili_user_videos": action_user_videos,
    "bili_video_info": action_video_info,
    "bili_search_user": action_search_user,
}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def emit(payload):
    """stdout 只允许出现这一行 JSON。"""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bilibili data layer for dsh-plugin-bilibili")
    parser.add_argument("--action", required=True, choices=sorted(ACTIONS.keys()))
    args = parser.parse_args(argv)

    if sys.version_info < (3, 10):
        emit({"code": -1, "message": "需要 Python 3.10+，当前为 %s" % (sys.version.split()[0],)})
        return 0

    if not read_cookie():
        emit({"code": -1, "message": "未在 .env 中配置 BILI_COOKIE"})
        return 0

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("stdin 必须是 JSON 对象")
    except Exception as exc:
        emit({"code": -1, "message": "工具参数解析失败：%s" % (exc,)})
        return 0

    action = ACTIONS[args.action]
    try:
        data = action(payload)
        emit({"code": 0, "data": data})
    except BiliError as exc:
        emit({"code": exc.code, "message": exc.message})
    except Exception as exc:  # 兜底：不让宿主只看到 traceback
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit({"code": -1, "message": "内部错误：%s" % (exc,)})
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
