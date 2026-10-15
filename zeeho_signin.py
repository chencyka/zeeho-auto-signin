#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ZEEHO 极核每日自动签到（GitHub Actions / 本地通用）

接口来源：H5 前端 https://h5.zeehoev.com/activity/signin
签到流程参考 MonicaGmm/zeeho-quanx（Quantumult X 版），本文件是其 Python 移植。

特性
----
* 纯标准库实现，无需 pip install 任何依赖
* 多账号（逗号 / 换行分隔的 Token）
* 失败自动重试
* 签到后回查真实状态（签到接口返回码不可信，以回查为准）
* 输出 GitHub Actions Step Summary，推送通知可选

用法
----
    export ZEEHO_TOKENS="uuid1,uuid2"      # 或从文件读取，见 --token-file
    python zeeho_signin.py                 # 执行签到
    python zeeho_signin.py --status-only   # 只查状态，不签到
    python zeeho_signin.py --dry-run       # 打印将要发出的请求，不真正发送

Token 获取
----------
    极核 App 已登录状态下，抓包任意业务请求头：
        Authorization: Bearer xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    或签到页地址：
        https://h5.zeehoev.com/activity/signin?token=<uuid>
    只要 UUID 那一段。

免责声明：仅供个人学习与自用，请勿用于商业用途、批量账号或任何违规场景。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# ============================================================================
# 配置区
# ============================================================================

HOST = "https://h5.zeehoev.com"

# H5 通道密钥。2026-10-10 服务端换发，旧值 AppID=Sw5F9uJi 已失效（返回 30125 permit error）。
# 若签到全部报 30125 / 30121 / permit error，说明服务端又轮换了密钥，
# 需要重新抓包替换下面两个值。
APP_ID = "AiTXmBrm"
APP_SECRET = "70c2c7458ab88ca9504ad0521f170075bc91f2f7"

# 接口路径（2026-10-10 起签到相关接口迁移到 /cfmotoservermine/H5/ 前缀）
PATH_BASE_INFO = "/cfmotoservermine/baseInfo"
PATH_SIGNIN = "/cfmotoservermine/H5/signin"
PATH_SIGNIN_INFO = "/cfmotoservermine/H5/signin/info"

# 设备指纹。deviceId 服务端目前不校验，删掉那段也行。
ZEEHO_UA_TEMPLATE = (
    "MOBILE|iOS|18.7|ZEEHO_APP|3.0.4|iPhone|iPhone 17 Pro|402*874|{DEVICE_ID}|unknown|iOS"
)
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 zeeho/3.0.4  (iPhone 17 Pro Build/27.0)iPhone"
)

# 成功返回码（接口里 code 是字符串）
OK_CODES = {"10000", "0", "200"}

RETRY = 2                 # 单账号失败重试次数
RETRY_DELAY = 1.5         # 重试间隔（秒）
TIMEOUT = 20              # 单次请求超时（秒）

# 业务时区：极核按中国时间判定"今天"
TZ = timezone(timedelta(hours=8))

# Token 失效相关的错误码
PERMIT_CODES = {"30125", "30121"}


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class AccountResult:
    tag: str = ""
    ok: bool = False
    dup: bool = False
    msg: str = ""
    days: Optional[int] = None          # 连续签到天数
    today_score: Optional[str] = None   # 今日获得积分
    month_signed: Optional[int] = None  # 本月已签天数
    avatar: str = ""
    net_fail: bool = False              # 网络层失败（大概率是分流问题）


@dataclass
class Stats:
    total: int = 0
    ok: int = 0
    dup: int = 0
    fail: int = 0
    net_fail: int = 0


# ============================================================================
# 工具函数
# ============================================================================

def log(msg: str) -> None:
    print(f"[ZEEHO] {msg}", flush=True)


def now_ms() -> str:
    return str(int(time.time() * 1000))


def today_str() -> str:
    """按中国时区取今天，格式 YYYY-MM-DD。"""
    return datetime.now(TZ).strftime("%Y-%m-%d")


def month_str() -> str:
    """按中国时区取当前月，格式 YYYY-M。"""
    d = datetime.now(TZ)
    return f"{d.year}-{d.month}"


def mask_token(token: str) -> str:
    """打码显示，避免日志泄露完整凭据。"""
    token = str(token)
    if len(token) > 16:
        return token[:8] + "…" + token[-4:]
    return "****"


def sign(query_str: str, body_str: str) -> Dict[str, str]:
    """
    复刻 H5 前端拦截器的签名逻辑：
        v = 查询串 + 请求体 + "appId=..&nonce=..&timestamp=.." + appSecret
        Cfmoto-X-Sign = md5(sha1(v))
    """
    nonce = str(uuid.uuid4())
    ts = now_ms()
    v = (
        query_str
        + body_str
        + f"appId={APP_ID}&nonce={nonce}&timestamp={ts}"
        + APP_SECRET
    )
    digest = hashlib.md5(hashlib.sha1(v.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    return {
        "sign": digest,
        "nonce": nonce,
        "timestamp": ts,
    }


def build_headers(token: str, query_str: str, body_str: str) -> Dict[str, str]:
    s = sign(query_str, body_str)
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Origin": HOST,
        "Referer": f"{HOST}/activity/signin",
        "User-Agent": UA,
        "Zeeho-User-Agent": ZEEHO_UA_TEMPLATE.replace("{DEVICE_ID}", str(uuid.uuid4()).upper()),
        "Authorization": f"Bearer {token}",
        "Cfmoto-X-Sign": s["sign"],
        "Cfmoto-X-Sign-Type": "0",
        "Cfmoto-X-Param": f"appId={APP_ID}&nonce={s['nonce']}&timestamp={s['timestamp']}",
    }


def stringify_query(params: Optional[Dict[str, Any]]) -> str:
    """只保留非空参数，按 k=v&k=v 拼接（不做 URL 编码，与服务端一致）。"""
    if not params:
        return ""
    parts = [
        f"{k}={v}"
        for k, v in params.items()
        if v is not None and v != ""
    ]
    return "&".join(parts)


def pick(obj: Any, path: str) -> Any:
    """按 data.xxx.yyy 路径取值。"""
    if not obj or not path:
        return None
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def is_ok(json_body: Any) -> bool:
    if not isinstance(json_body, dict):
        return False
    code = json_body.get("code", json_body.get("status"))
    return str(code) in OK_CODES


# ============================================================================
# 请求层
# ============================================================================

@dataclass
class ApiResponse:
    status: int = 0
    raw: str = ""
    json: Optional[dict] = None
    err: str = ""

    @property
    def json_null(self) -> bool:
        return self.json is None


def api(
    path: str,
    method: str,
    token: str,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    dry_run: bool = False,
) -> ApiResponse:
    query_str = stringify_query(query)
    body_str = "" if body is None else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
    url = HOST + path + (f"?{query_str}" if query_str else "")
    headers = build_headers(token, query_str, body_str)

    if dry_run:
        log(f"[DRY-RUN] {method} {url}")
        for k, v in headers.items():
            shown = mask_token(v) if k == "Authorization" else v
            log(f"    {k}: {shown}")
        if body_str:
            log(f"    body: {body_str}")
        return ApiResponse(status=200, raw="{}", json={"code": "10000", "data": {}})

    data = body_str.encode("utf-8") if (method not in ("GET", "DELETE") and body_str) else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        status = e.code
    except Exception as e:  # 网络层失败
        return ApiResponse(status=0, raw="", json=None, err=str(e))

    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None
    return ApiResponse(status=status, raw=raw, json=parsed)


def api_retry(
    path: str,
    method: str,
    token: str,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    dry_run: bool = False,
) -> ApiResponse:
    attempt = 0
    while True:
        res = api(path, method, token, query, body, dry_run)
        bad = res.status == 0 or res.status >= 500 or res.json is None
        if not bad or attempt >= RETRY:
            return res
        attempt += 1
        log(f"    第 {attempt} 次重试…")
        time.sleep(RETRY_DELAY)


# ============================================================================
# 业务逻辑
# ============================================================================

def fetch_calendar(token: str, dry_run: bool = False) -> Dict[str, Any]:
    """拉本月签到日历，统计「今日获得积分」与「本月已签天数」。"""
    res = api_retry(PATH_SIGNIN_INFO, "GET", token, {"month": month_str()}, dry_run=dry_run)
    if not res.json or not is_ok(res.json):
        return {}
    details = pick(res.json, "data.nowSignDetailVos") or []
    if not isinstance(details, list):
        details = []
    today = today_str()
    today_score = None
    signed = 0
    for d in details:
        if not isinstance(d, dict):
            continue
        # signStatue === 3 表示已签到
        try:
            if int(d.get("signStatue")) == 3:
                signed += 1
        except (TypeError, ValueError):
            pass
        if str(d.get("createDate", "")).startswith(today):
            s = d.get("integralScore")
            if s is not None and s != "":
                today_score = s
    return {"today_score": today_score, "month_signed": signed}


def run_one(token: str, idx: int, status_only: bool = False, dry_run: bool = False) -> AccountResult:
    tag = f"账号{idx + 1}"
    result = AccountResult(tag=tag)

    # 1. 拉用户信息（顺便拿昵称和头像；此接口不需要 user_id 头）
    r1 = api_retry(PATH_BASE_INFO, "GET", token, dry_run=dry_run)
    if r1.json is None:
        result.net_fail = True
        result.msg = (
            f"HTTP {r1.status}" if r1.status else "请求发不出去"
        ) + (f"（{r1.err}）" if r1.err else "")
        return result

    if is_ok(r1.json):
        nickname = pick(r1.json, "data.nickName")
        if nickname:
            result.tag = f"账号{idx + 1}({nickname})"
        result.avatar = pick(r1.json, "data.avatar") or ""
    elif r1.status in (401, 403):
        result.msg = "Token 失效，请重新抓包"
        return result

    # 2. 查签到状态，判断今天是否已签
    r2 = api_retry(PATH_SIGNIN, "GET", token, dry_run=dry_run)
    if r2.json and is_ok(r2.json):
        result.days = pick(r2.json, "data.continueDays")
        last = str(pick(r2.json, "data.lastTime") or "")
        if last.startswith(today_str()):
            result.ok = True
            result.dup = True
            result.msg = "今日已签到"
            return result

    if status_only:
        result.ok = True
        result.msg = f"未签到（连签 {result.days} 天）" if result.days is not None else "未签到"
        return result

    # 3. 执行签到
    #    注意：签到接口返回码不可信 —— 新签和重复签都可能是 code "10000"
    #    或 "repeatedly_operation"，所以不看返回码下结论，一律回查。
    r3 = api_retry(PATH_SIGNIN, "POST", token, dry_run=dry_run)
    post_code = str(r3.json.get("code")) if (r3.json and r3.json.get("code") is not None) else ""
    post_msg = (r3.json.get("message") or r3.json.get("msg") or "") if r3.json else ""

    if r3.json is None:
        result.msg = f"HTTP {r3.status} 返回非 JSON"
        return result

    # 4. 回查签到状态，以它为准
    r4 = api_retry(PATH_SIGNIN, "GET", token, dry_run=dry_run)
    last2 = str(pick(r4.json, "data.lastTime") or "") if r4.json else ""
    if r4.json:
        result.days = pick(r4.json, "data.continueDays")

    if last2.startswith(today_str()):
        result.ok = True
        if post_code == "" or post_code in OK_CODES:
            result.msg = "签到成功"
        else:
            result.dup = True
            result.msg = "今日已签到"
        return result

    result.ok = False
    is_permit = post_code in PERMIT_CODES or "permit error" in str(post_msg).lower()
    result.msg = (
        f"签到未生效：{post_msg or '未知'}"
        + (f" (code {post_code})" if post_code else "")
        + ("\n     服务端接口凭证已变更，请更新 APP_ID / APP_SECRET" if is_permit else "")
    )
    return result


def load_tokens(args: argparse.Namespace) -> List[str]:
    """按优先级取 Token：命令行 > 环境变量 > 文件。"""
    raw = ""

    if args.token:
        raw = args.token
        log("Token 来源: 命令行参数")
    else:
        env = os.environ.get("ZEEHO_TOKENS", "")
        if env.strip():
            raw = env
            log("Token 来源: 环境变量 ZEEHO_TOKENS")
        elif args.token_file and os.path.isfile(args.token_file):
            with open(args.token_file, "r", encoding="utf-8") as f:
                raw = f.read()
            log(f"Token 来源: 文件 {args.token_file}")

    tokens = [t.strip() for t in re.split(r"[\n,]+", raw) if t.strip()]
    # 兼容 "Bearer xxx" 或整条 URL 的粘贴
    cleaned = []
    for t in tokens:
        t = re.sub(r"^\s*[Bb]earer\s+", "", t).strip()
        m = re.search(r"[?&]token=([^&#\s]+)", t)
        if m:
            t = m.group(1)
        cleaned.append(t)
    return cleaned


# ============================================================================
# 输出
# ============================================================================

def write_summary(results: List[AccountResult], stats: Stats) -> None:
    """写入 GitHub Actions Step Summary。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "# ZEEHO 极核每日签到",
        "",
        f"- 时间：{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')} (UTC+8)",
        f"- 账号：{stats.total} 个（成功 {stats.ok} · 已签 {stats.dup} · 失败 {stats.fail}）",
        "",
        "| 账号 | 结果 | 连签 | 今日积分 | 本月已签 | 说明 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        icon = "✅" if r.ok and not r.dup else ("➖" if r.dup else "❌")
        lines.append(
            f"| {r.tag} | {icon} | {r.days if r.days is not None else '-'} "
            f"| {r.today_score if r.today_score is not None else '-'} "
            f"| {r.month_signed if r.month_signed is not None else '-'} "
            f"| {r.msg} |"
        )
    if stats.net_fail:
        lines += [
            "",
            "> ⚠️ 有账号网络层失败。`h5.zeehoev.com` 是国内域名，"
            "若运行环境走了代理请改为直连。",
        ]
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log(f"写入 Step Summary 失败: {e}")


def notify(title: str, body: str) -> None:
    """
    可选的推送通知。按需配置环境变量：
        NOTIFY_BARK      Bark 推送，例如 https://api.day.app/你的key
        NOTIFY_SERVERCHAN  Server酱 SendKey
    都不配则只写日志。
    """
    bark = os.environ.get("NOTIFY_BARK", "").strip()
    if bark:
        try:
            url = f"{bark.rstrip('/')}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
            urllib.request.urlopen(url, timeout=10).read()
        except Exception as e:
            log(f"Bark 推送失败: {e}")

    sct = os.environ.get("NOTIFY_SERVERCHAN", "").strip()
    if sct:
        try:
            data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
            urllib.request.urlopen(
                f"https://sctapi.ftqq.com/{sct}.send", data=data, timeout=10
            ).read()
        except Exception as e:
            log(f"Server酱推送失败: {e}")


# ============================================================================
# 主入口
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="ZEEHO 极核每日自动签到")
    parser.add_argument("--token", help="单个 Token（优先于环境变量）")
    parser.add_argument("--token-file", help="Token 列表文件，每行一个")
    parser.add_argument("--status-only", action="store_true", help="只查状态，不执行签到")
    parser.add_argument("--dry-run", action="store_true", help="打印请求详情，不真正发送")
    args = parser.parse_args()

    tokens = load_tokens(args)
    if not tokens:
        log("未配置 Token。请设置环境变量 ZEEHO_TOKENS，或用 --token / --token-file 传入。")
        return 1

    log(f"共 {len(tokens)} 个账号，模式：{'只查状态' if args.status_only else '签到'}"
        + ("（dry-run）" if args.dry_run else ""))

    results: List[AccountResult] = []
    for i, tk in enumerate(tokens):
        log(f"--- {i + 1}/{len(tokens)}  Token={mask_token(tk)} ---")
        r = run_one(tk, i, status_only=args.status_only, dry_run=args.dry_run)

        # 补一次本月日历，拿「今日获得积分」和「本月已签天数」
        if not args.dry_run:
            cal = fetch_calendar(tk)
            if cal:
                r.today_score = cal.get("today_score")
                r.month_signed = cal.get("month_signed")

        results.append(r)
        log(f"    → {'成功' if r.ok else '失败'}  {r.msg}")

    # 汇总
    stats = Stats(total=len(results))
    for r in results:
        if r.net_fail:
            stats.net_fail += 1
        if r.ok:
            stats.ok += 1
            if r.dup:
                stats.dup += 1
        else:
            stats.fail += 1

    all_dup = stats.ok > 0 and all(r.dup for r in results if r.ok)
    if stats.ok == 0:
        title = f"签到失败 (0/{stats.total})"
    elif all_dup:
        title = f"今日已签到 ({stats.ok}/{stats.total})"
    elif stats.ok == stats.total:
        title = f"签到成功 ({stats.ok}/{stats.total})"
    else:
        title = f"部分成功 ({stats.ok}/{stats.total})"

    log("")
    log(f"===== {title} =====")
    for r in results:
        extra = []
        if r.days is not None:
            extra.append(f"连续 {r.days} 天")
        if r.today_score is not None and r.today_score != "":
            extra.append(f"今日 +{r.today_score} 积分")
        if r.month_signed is not None:
            extra.append(f"本月已签 {r.month_signed} 天")
        line = f"  {r.tag}: {r.msg}"
        if extra:
            line += "\n      " + " · ".join(extra)
        print(line)

    write_summary(results, stats)

    body_lines = [f"{r.tag}: {r.msg}" for r in results]
    if stats.net_fail:
        body_lines.append("")
        body_lines.append("⚠️ 连不上 h5.zeehoev.com，国内域名被代理了，请改为直连。")
    notify("ZEEHO 极核 · 每日签到", title + "\n" + "\n".join(body_lines))

    return 0 if stats.fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
