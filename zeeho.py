# -*- coding: utf-8 -*-
"""
ZEEHO 极核 · 每日自动化（先签到，后互动）
==========================================

一个脚本跑完两件事，一次 Actions 运行搞定：

    ① 签到    →  +1 分（连签另有盲盒）
    ② 互动    →  发帖 +1 / 点赞 +1 / 分享 +1，最多 3 分

两件事走的是**完全不同的两套后端**，凭证、签名细节都不一样：

| 项       | 签到                    | 互动                          |
|----------|-------------------------|-------------------------------|
| 域名     | h5.zeehoev.com（H5 端） | tapi.zeehoev.com（App 端）    |
| appId    | AiTXmBrm                | S7qPWPU1                      |
| appSecret| 70c2c7458ab88ca95…      | c5e0da7f4da28df80…            |
| nonce    | UUID                    | <毫秒时间戳>+16 位随机字符    |
| query    | 不排序                  | **按 key 字典序排序**         |
| 额外头   | Origin / Referer / Zeeho-User-Agent | user_id / interfaceversion |

签名公式两端一致：`sign = md5(sha1(查询串 + 请求体 + param + secret))`
请求体必须是紧凑 JSON；DELETE 时 body 强制为空。

流程（每个账号）：
    签到 → （成功才继续）→ 发帖 → 点赞 → 分享 → 领分享积分 → 删除临时帖

删除带硬保护：只有本次自己创建的动态才会删，`ZEEHO_POST_ID` 指定的既有动态绝不删。

配置
----
ZEEHO_TOKENS       : <token>:<userId>，多账号用 @ 分隔。纯 token 也能跑
ZEEHO_POST_ID      : 可选。指定已有动态 ID 或分享链接；填了就只点赞不删帖
ZEEHO_POST_CONTENT : 可选。临时帖内容，默认「开心的一天」
INTERACT_STEPS     : 可选。逗号分隔 publish,like,share,delete
ENABLE_COMMUNITY_LIKE : 设为 1，拿不到动态时才去赞推荐流热门帖（默认关闭）

用法
----
    python zeeho.py --dry-run                  # 自检，不联网
    python zeeho.py --signin-only              # 只签到
    python zeeho.py --interact-only            # 只互动
    python zeeho.py --steps publish,like       # 自定义互动步骤
    python zeeho.py --resolve-uid              # 自助获取 userId
"""
import os, re, sys, ssl, json, time, uuid, base64, random, hashlib, argparse, http.client, urllib.parse, urllib.request, urllib.error
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ============================================================================
# 通用
# ============================================================================
TIMEOUT = 20
MAX_RETRY = 2
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY", "")


def log(m: str = ""):
    print(m, flush=True)


def mask_token(t) -> str:
    t = str(t or "")
    return (t[:8] + "…" + t[-6:]) if len(t) > 18 else t


def now_ms() -> str:
    return str(int(time.time() * 1000))


def today_str() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))


def month_str() -> str:
    return today_str()[:7]


def nap(a: int = 2, b: int = 4):
    """业务步骤之间的随机间隔，模拟真人节奏"""
    time.sleep(random.randint(a, b))


def parse_body(txt: str) -> Dict[str, Any]:
    try:
        return json.loads(txt)
    except Exception:
        pass
    # 鉴权层返回的是 XML：<InvalidTokenException><error>invalid_token</error>…
    if "invalid_token" in txt:
        return {"code": "401", "message": "invalid_token（Token 无效或已过期）"}
    return {"_raw": txt[:300], "code": "-2", "message": "非 JSON 响应"}


def md5_sha1(s: str) -> str:
    return hashlib.md5(hashlib.sha1(s.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()


# ============================================================================
# H5 端 —— 签到
# ============================================================================
H5_HOST = "h5.zeehoev.com"
H5_APP_ID = "AiTXmBrm"
H5_APP_SECRET = "70c2c7458ab88ca9504ad0521f170075bc91f2f7"
H5_UA = "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
ZEEHO_UA_TEMPLATE = (
    "zeeho/{ver} (Android; {model}; Android {os}; Scale/3.00) "
    "deviceId={DEVICE_ID} channel=official"
)
PATH_BASE_INFO = "/cfmotoservermine/baseInfo"
PATH_SIGNIN = "/cfmotoservermine/H5/signin"
PATH_SIGNIN_INFO = "/cfmotoservermine/H5/signin/info"
OK_CODES = {"10000", "0", "200"}
PERMIT_CODES = {"30125", "30121", "30124"}


@dataclass
class ApiResponse:
    status: int = 0
    raw: str = ""
    json: Optional[Dict[str, Any]] = None
    err: str = ""


def h5_sign(query_str: str, body_str: str):
    nonce = str(uuid.uuid4())
    ts = now_ms()
    v = query_str + body_str + "appId=%s&nonce=%s&timestamp=%s" % (H5_APP_ID, nonce, ts) + H5_APP_SECRET
    return md5_sha1(v), nonce, ts


def h5_headers(token: str, query_str: str, body_str: str) -> Dict[str, str]:
    sign, nonce, ts = h5_sign(query_str, body_str)
    return {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Origin": "https://" + H5_HOST,
        "Referer": "https://%s/activity/signin" % H5_HOST,
        "User-Agent": H5_UA,
        "Zeeho-User-Agent": ZEEHO_UA_TEMPLATE.replace("{DEVICE_ID}", str(uuid.uuid4()).upper())
                                             .replace("{ver}", "3.0.5").replace("{model}", "Pixel 7")
                                             .replace("{os}", "13"),
        "Authorization": "Bearer " + token,
        "Cfmoto-X-Sign": sign,
        "Cfmoto-X-Sign-Type": "0",
        "Cfmoto-X-Param": "appId=%s&nonce=%s&timestamp=%s" % (H5_APP_ID, nonce, ts),
    }


def stringify_query(params: Optional[Dict[str, Any]]) -> str:
    """只保留非空参数，按 k=v&k=v 拼接（不做 URL 编码，与服务端一致）。"""
    if not params:
        return ""
    return "&".join("%s=%s" % (k, v) for k, v in params.items() if v is not None and v != "")


def h5_call(path: str, method: str, token: str, query: Optional[Dict[str, Any]] = None,
            body: Optional[Any] = None, dry_run: bool = False) -> ApiResponse:
    query_str = stringify_query(query)
    body_str = "" if body is None else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))
    url = "https://" + H5_HOST + path + (("?" + query_str) if query_str else "")
    headers = h5_headers(token, query_str, body_str)

    if dry_run:
        log("    [DRY] %s %s" % (method, url))
        if body_str:
            log("          body: %s" % body_str)
        return ApiResponse(status=200, raw="{}", json={"code": "10000", "data": {}})

    data = body_str.encode("utf-8") if (method not in ("GET", "DELETE") and body_str) else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        status = e.code
    except Exception as e:
        return ApiResponse(status=0, raw="", json=None, err=str(e))
    return ApiResponse(status=status, raw=raw, json=parse_body(raw))


def h5_retry(path, method, token, query=None, body=None, dry_run=False) -> ApiResponse:
    for attempt in range(MAX_RETRY + 1):
        res = h5_call(path, method, token, query, body, dry_run)
        bad = res.status == 0 or res.status >= 500 or res.json is None
        if not bad or attempt >= MAX_RETRY:
            return res
        log("    第 %d 次重试…" % (attempt + 1))
        time.sleep(2)


def pick(obj: Any, path: str) -> Any:
    cur = obj
    for seg in path.split("."):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            return None
    return cur


def is_ok(j: Optional[Dict[str, Any]]) -> bool:
    if not j:
        return False
    return str(j.get("code")) in OK_CODES


# ============================================================================
# App 端 —— 互动
# ============================================================================
APP_HOST = "tapi.zeehoev.com"
APP_ID = "S7qPWPU1"
APP_SECRET = "c5e0da7f4da28df805694ec3dd1fc6792e9df99d"
APP_UA = "ZEEHO/5.0 (iPhone; iOS 17.0; Scale/3.00)"
OK_CODE = "10000"

P_MINE_ARTICLES = "/v1.0/social/cfmotoserversocial/community/mineArticleInfo"
P_COMMUNITY = "/v1.0/social/cfmotoserversocial/community/qbTzInfoNewV2"
P_LIKE = "/v1.0/social/cfmotoserversocial/socialCommu/likeFavoriteInfo"
P_SETTING = "/v1.0/mine/cfmotoservermine/setting/{uid}"
P_TOTAL_INTEGRAL = "/v1.0/mine/cfmotoservermine/integral/totalIntegral"
P_PUBLISH = "/v1.0/social/cfmotoserversocial/commonArticle"
P_SHARE = "/v1.0/social/cfmotoserversocial/article/share/{pid}"
P_DELETE = "/v1.0/social/cfmotoserversocial/commonArticle/deleteArticle"
P_ADJUST_SHARE = "/v1.0/mine/cfmotoservermine/integral/adjustByShare"

DEFAULT_CONTENT = "开心的一天"
ALL_STEPS = ("publish", "like", "share", "delete")


def app_nonce(ts: str) -> str:
    """App 端 nonce = <毫秒时间戳> + 16 位随机字母数字（H5 端才是 UUID）"""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "%s%s" % (ts, "".join(random.choice(chars) for _ in range(16)))


def to_query(params: Dict[str, Any]) -> str:
    """照 SignUtil.toQuery：只跳过 None，然后**按 key 字典序排序**再拼。"""
    keys = sorted([k for k, v in params.items() if v is not None])
    return "&".join("%s=%s" % (k, params[k]) for k in keys)


def app_call(method: str, path: str, token: str, uid: str = "",
             params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None,
             dry_run: bool = False):
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    query_str = to_query(params)
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    ts = now_ms()
    nonce = app_nonce(ts)
    param = "appId=%s&nonce=%s&timestamp=%s" % (APP_ID, nonce, ts)
    # DELETE 时 body 强制为空（与 SignUtil 的 "DELETE".equals 分支一致）
    eff_body = "" if method == "DELETE" else body_str
    sign = md5_sha1(query_str + eff_body + param + APP_SECRET)

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept-Language": "zh-CN",
        "Authorization": "Bearer " + token,
        "User-Agent": APP_UA,
        "user_id": str(uid or ""),
        "interfaceversion": "2",
        "cfmoto-x-param": param,
        "cfmoto-x-sign": sign,
        "cfmoto-x-sign-type": "0",
        "timestamp": ts,
        "nonce": nonce,
        "signature": sign,
    }
    url = path + (("?" + query_str) if query_str else "")

    if dry_run:
        log("    [DRY] %s %s" % (method, url))
        if body_str:
            log("          body: %s" % body_str)
        return 200, {"code": OK_CODE, "message": "dry-run", "data": {}}

    payload = body_str.encode("utf-8") if (method != "GET" and body_str) else None
    for attempt in range(MAX_RETRY + 1):
        try:
            conn = http.client.HTTPSConnection(APP_HOST, timeout=TIMEOUT, context=CTX)
            conn.request(method, url, body=payload, headers=headers)
            r = conn.getresponse()
            txt = r.read().decode("utf-8", "ignore")
            conn.close()
            return r.status, parse_body(txt)
        except Exception as e:
            if attempt >= MAX_RETRY:
                return -1, {"code": "-1", "message": "网络异常: %s" % e}
            time.sleep(2)


# ============================================================================
# 账号解析
# ============================================================================
@dataclass
class Account:
    token: str
    userId: str = ""
    index: int = 0

    @property
    def tag(self) -> str:
        return "账号%d" % self.index


def clean_token(t: str) -> str:
    t = str(t or "").strip().strip('"').strip("'")
    t = re.sub(r"^\s*[Bb]earer\s+", "", t).strip()
    m = re.search(r"[?&]token=([^&#\s]+)", t)
    return m.group(1) if m else t


def parse_accounts(raw: str) -> List[Account]:
    """
    支持三种写法（可混用）：
      1) "<token>:<userId>@<token2>:<userId2>"      推荐
      2) "token1@token2" + ZEEHO_USER_IDS="uid1,uid2"
      3) "token1,token2" / 每行一个（兼容签到脚本的老写法）
    """
    uids = [x.strip() for x in re.split(r"[,;\s]+", os.environ.get("ZEEHO_USER_IDS", "") or "") if x.strip()]
    out: List[Account] = []
    chunks: List[str] = []
    for part in re.split(r"[\n,]+", raw or ""):
        chunks.extend([c for c in part.split("@") if c.strip()])
    for i, c in enumerate(chunks):
        c = c.strip().strip('"').strip("'")
        if not c:
            continue
        if ":" in c and re.search(r":\s*\d{6,20}\s*$", c):
            tk, _, uid = c.rpartition(":")
        else:
            tk, uid = c, (uids[i] if i < len(uids) else "")
        out.append(Account(token=clean_token(tk), userId=uid.strip(), index=len(out) + 1))
    return out


DIGITS = r"\d{6,20}"
UID_KEYS = ("userId", "id", "uid", "memberId", "accountId", "custId", "userNo")


def deep_find_uid(obj, depth=0):
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for k in UID_KEYS:
            v = obj.get(k)
            if isinstance(v, (int, str)) and re.fullmatch(DIGITS, str(v).strip()):
                return str(v).strip()
        for v in obj.values():
            r = deep_find_uid(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for it in obj[:3]:
            r = deep_find_uid(it, depth + 1)
            if r:
                return r
    return None


def b64_decode(s: str) -> str:
    try:
        s = s.strip()
        return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "ignore")
    except Exception:
        return ""


def post_id_from_url(url: str) -> str:
    """动态分享链接 → postId。uuid 参数是 postId 的 Base64。"""
    m = re.search(r"[?&]uuid=([^&#]+)", url)
    if m:
        dec = b64_decode(urllib.parse.unquote(m.group(1)))
        if dec.isdigit():
            return dec
    m = re.search(r"\b(\d{15,20})\b", url)
    return m.group(1) if m else ""


def parse_post_ids(raw: str, n: int) -> List[str]:
    """支持 @ 分隔按账号对应；环境变量里允许直接贴分享链接"""
    if raw:
        for piece in re.split(r"[\s,]+", raw.strip()):
            if piece.startswith("http"):
                raw = raw.replace(piece, post_id_from_url(piece) or piece)
    parts = [x.strip() for x in (raw or "").split("@") if x.strip()]
    if not parts:
        return [""] * n
    if len(parts) == 1:
        return parts * n
    return [parts[i] if i < len(parts) else "" for i in range(n)]


# ============================================================================
# 业务一：签到
# ============================================================================
@dataclass
class SigninResult:
    ok: bool = False
    dup: bool = False
    days: Any = None
    today_score: Any = None
    month_signed: Any = None
    msg: str = ""
    net_fail: bool = False
    token_expired: bool = False
    tag: str = ""


def fetch_calendar(token: str, dry_run: bool = False) -> Dict[str, Any]:
    """本月签到日历：今日获得积分 + 本月已签天数"""
    res = h5_retry(PATH_SIGNIN_INFO, "GET", token, {"month": month_str()}, dry_run=dry_run)
    if not res.json or not is_ok(res.json):
        return {}
    details = pick(res.json, "data.nowSignDetailVos") or []
    if not isinstance(details, list):
        details = []
    today, today_score, signed = today_str(), None, 0
    for d in details:
        if not isinstance(d, dict):
            continue
        try:
            if int(d.get("signStatue")) == 3:  # 3 = 已签到
                signed += 1
        except (TypeError, ValueError):
            pass
        if str(d.get("createDate", "")).startswith(today):
            s = d.get("integralScore")
            if s not in (None, ""):
                today_score = s
    return {"today_score": today_score, "month_signed": signed}


def do_signin(acc: Account, status_only=False, dry_run=False) -> SigninResult:
    r = SigninResult(tag=acc.tag)

    # 1) 用户信息（顺便拿昵称；此接口不需要 user_id 头）
    r1 = h5_retry(PATH_BASE_INFO, "GET", acc.token, dry_run=dry_run)
    if r1.json is None:
        r.net_fail = True
        r.msg = ("HTTP %s" % r1.status if r1.status else "请求发不出去") + (("（%s）" % r1.err) if r1.err else "")
        return r
    if is_ok(r1.json):
        nick = pick(r1.json, "data.nickName")
        if nick:
            r.tag = "%s(%s)" % (acc.tag, nick)
    elif r1.status in (401, 403) or str(r1.json.get("code")) == "401":
        r.token_expired = True
        r.msg = "Token 失效，请重新抓包"
        return r

    # 2) 查状态，判断今天是否已签
    r2 = h5_retry(PATH_SIGNIN, "GET", acc.token, dry_run=dry_run)
    if r2.json and is_ok(r2.json):
        r.days = pick(r2.json, "data.continueDays")
        if str(pick(r2.json, "data.lastTime") or "").startswith(today_str()):
            r.ok = r.dup = True
            r.msg = "今日已签到"
            return r

    if status_only:
        r.ok = True
        r.msg = "未签到（连签 %s 天）" % r.days if r.days is not None else "未签到"
        return r

    # 3) 执行签到。返回码不可信（新签/重签都可能 10000），一律回查
    r3 = h5_retry(PATH_SIGNIN, "POST", acc.token, dry_run=dry_run)
    post_code = str(r3.json.get("code")) if (r3.json and r3.json.get("code") is not None) else ""
    post_msg = (r3.json.get("message") or r3.json.get("msg") or "") if r3.json else ""
    if r3.json is None:
        r.msg = "HTTP %s 返回非 JSON" % r3.status
        return r

    # 4) 回查
    r4 = h5_retry(PATH_SIGNIN, "GET", acc.token, dry_run=dry_run)
    if r4.json:
        r.days = pick(r4.json, "data.continueDays")
    if str(pick(r4.json, "data.lastTime") or "").startswith(today_str()):
        r.ok = True
        r.msg = "签到成功" if (post_code == "" or post_code in OK_CODES) else "今日已签到"
        r.dup = post_code not in ("",) and post_code not in OK_CODES
        return r

    r.msg = "签到未生效：%s%s" % (post_msg or "未知", (" (code %s)" % post_code) if post_code else "")
    if post_code in PERMIT_CODES or "permit error" in str(post_msg).lower():
        r.msg += "\n     服务端接口凭证已变更，请更新 H5 的 APP_ID / APP_SECRET"
    return r


# ============================================================================
# 业务二：互动（发帖 / 点赞 / 分享）
# ============================================================================
@dataclass
class InteractResult:
    ok: bool = False
    published: bool = False
    liked: bool = False
    shared: bool = False
    deleted: bool = False
    postId: str = ""
    score: Any = None
    score_after: Any = None
    gain: Any = None
    uid: str = ""
    detail: List[str] = field(default_factory=list)
    err: str = ""


def autofill_uid(acc: Account) -> str:
    """用同一个 token 打 H5 端用户信息接口，返回体里通常就带数字 id。"""
    res = h5_retry(PATH_BASE_INFO, "GET", acc.token)
    if res.json and is_ok(res.json):
        uid = deep_find_uid(res.json)
        if uid:
            log("    userId 自动解析: %s" % uid)
            return uid
    return ""


def fetch_score(token: str, uid: str):
    """积分余额。优先走 totalIntegral（不需要 userId），失败再退 setting/{uid}。"""
    st, d = app_call("GET", P_TOTAL_INTEGRAL, token, uid)
    if st == 200 and str(d.get("code")) == OK_CODE:
        v = d.get("data")
        if isinstance(v, dict):
            for k in ("score", "totalIntegral", "integral", "usableIntegral"):
                if isinstance(v.get(k), int):
                    return v[k]
        elif isinstance(v, int):
            return v
    if uid:
        st2, d2 = app_call("GET", P_SETTING.format(uid=uid), token, uid)
        if st2 == 200 and isinstance(d2.get("data"), dict):
            s = d2["data"].get("score")
            if isinstance(s, int):
                return s
    return None


def resolve_uid(token: str):
    uid = autofill_uid(Account(token=token, index=1))
    if uid:
        print("\n请把下面这组填进 GitHub Secrets：\n    ZEEHO_TOKENS = %s:%s" % (token, uid))
        return uid
    print("\n未能自动取到 userId。请手机 MitM 监听 tapi.zeehoev.com，")
    print("打开「我的」页，看 /v1.0/mine/cfmotoservermine/setting/<数字> 里那段数字。")
    return None


def pick_rows(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("records", "list", "rows", "data"):
            v = d.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                r = pick_rows(v)
                if r:
                    return r
    return []


def extract_id(item):
    if not isinstance(item, dict):
        return None
    for k in ("postId", "postid", "articleId", "articleID", "uuid", "tuuid", "id", "dataId", "tid"):
        v = item.get(k)
        if v not in (None, "", 0):
            return str(v)
    return None


def pick_post_id(data, uid=""):
    rows = pick_rows(data)
    if not rows:
        return None
    if uid:
        for it in rows:
            if isinstance(it, dict) and str(it.get("userId") or it.get("createBy") or it.get("uid") or "") == str(uid):
                pid = extract_id(it)
                if pid:
                    return pid
    for it in rows:
        pid = extract_id(it)
        if pid:
            return pid
    return None


def do_interact(acc: Account, steps, allow_community=False, forced_post=None,
                content=DEFAULT_CONTENT, dry_run=False) -> InteractResult:
    token, uid = acc.token, acc.userId
    res = InteractResult(uid=uid)
    if not uid:
        uid = autofill_uid(acc)
        res.uid = uid

    res.score = fetch_score(token, uid)

    post = forced_post or None
    self_published = False  # 只有本次自己创建的才允许删除

    # ---- 1) 发帖 ----
    if "publish" in steps and not post:
        body = {"postSubInfo": {"topicList": []}, "topicid": "", "postcontent": content}
        st, d = app_call("POST", P_PUBLISH, token, uid, None, body, dry_run)
        if st == 200 and str(d.get("code")) == OK_CODE:
            self_published = True
            res.published = True
            post = extract_id(d.get("data"))
            res.detail.append("[发帖] %s%s" % (content, (" id=%s" % post) if post else ""))
        else:
            res.detail.append("[发帖] 失败: %s" % d.get("message", "HTTP %s" % st))
        nap()

    # ---- 2) 确定 postId ----
    if not post:
        p = {"page": 1, "pageSize": 10}
        if uid:
            p["userId"] = uid
        st, d = app_call("GET", P_MINE_ARTICLES, token, uid, p, None, dry_run)
        if st == 200 and str(d.get("code")) == OK_CODE:
            post = pick_post_id(d.get("data"), uid)
        res.detail.append("[查动态] postId=%s" % (post or "无"))

    # ---- 3) 兜底公共流（默认关闭）----
    if not post and allow_community:
        st, d = app_call("GET", P_COMMUNITY, token, uid,
                         {"page": 1, "pageSize": 20, "postModule": 2, "slidingType": 1}, None, dry_run)
        if st == 200 and str(d.get("code")) == OK_CODE:
            hot = sorted([r for r in pick_rows(d.get("data")) if isinstance(r, dict)],
                         key=lambda r: int(r.get("likeTimes") or 0), reverse=True)
            for r in hot:
                if uid and str(r.get("userId") or "") == str(uid):
                    continue
                pid = extract_id(r)
                if pid:
                    post = pid
                    break
        res.detail.append("[公共动态] postId=%s" % (post or "无"))

    if not post:
        res.err = "发不出动态，动态列表也是空的。"
        res.err += "可设 ENABLE_COMMUNITY_LIKE=1 去赞推荐流，或先手动发一条动态。"
        return res

    res.postId = post

    # ---- 4) 点赞 ----
    if "like" in steps:
        st, d = app_call("POST", P_LIKE, token, uid, None,
                         {"postId": str(post), "kindFlag": "0"}, dry_run)
        code, msg = str(d.get("code", "")), d.get("message", "")
        res.liked = st == 200 and code == OK_CODE
        res.detail.append("[点赞] %s -> %s" % (post, "成功" if res.liked else "%s %s" % (code, msg)))
        if not res.liked and code in PERMIT_CODES:
            res.err = "签名被拒(%s) —— App 端密钥可能已轮换" % code
        elif not res.liked and (st == 401 or code in ("401", "40001")):
            res.err = "Token 失效(%s)，请重新抓取" % (code or st)
        nap()

    # ---- 5) 分享 + 领积分 ----
    if "share" in steps:
        st, d = app_call("PUT", P_SHARE.format(pid=post), token, uid, None, None, dry_run)
        res.shared = st == 200 and str(d.get("code")) == OK_CODE
        res.detail.append("[分享] -> %s" % ("成功" if res.shared else d.get("message", "HTTP %s" % st)))
        nap()
        if res.shared:
            st2, d2 = app_call("GET", P_ADJUST_SHARE, token, uid, None, None, dry_run)
            res.detail.append("[分享积分] -> %s" % ("已领取" if st2 == 200 and str(d2.get("code")) == OK_CODE
                                                else d2.get("message", "")))

    # ---- 6) 删除本次临时帖 ----
    if "delete" in steps:
        if not self_published:
            res.detail.append("[删帖] 跳过：目标是既有动态，不是本次创建的")
        else:
            nap()
            st, d = app_call("DELETE", P_DELETE, token, uid,
                             {"articleId": post, "postType": "1"}, None, dry_run)
            res.deleted = st == 200 and str(d.get("code")) == OK_CODE
            res.detail.append("[删帖] -> %s" % ("已删除" if res.deleted else d.get("message", "HTTP %s" % st)))

    # ---- 7) 事后积分 ----
    after = fetch_score(token, uid)
    if after is not None:
        res.score_after = after
        if isinstance(after, int) and isinstance(res.score, int):
            res.gain = after - res.score
    return res


# ============================================================================
# 输出
# ============================================================================
def write_summary(rows: List[Dict[str, Any]]) -> None:
    if not SUMMARY_FILE:
        return
    def tick(b):
        return "是" if b else "—"

    def signin_cell(s):
        if not s:
            return "—"
        if s.dup:
            return "已签"
        return "成功" if s.ok else "失败"

    lines = [
        "## ZEEHO 极核 · 每日自动化（先签到，后互动）",
        "",
        "| 账号 | 签到 | 连签 | 今日积分 | 发帖 | 点赞 | 分享 | 删除 | 积分余额 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        s, i = row.get("signin"), row.get("interact")
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["tag"],
            signin_cell(s),
            (s.days if s and s.days is not None else "—"),
            (s.today_score if s and s.today_score is not None else "—"),
            (tick(i and i.published) if i else "—"),
            (tick(i and i.liked) if i else "—"),
            (tick(i and i.shared) if i else "—"),
            (tick(i and i.deleted) if i else "—"),
            ("%s → %s" % (i.score, i.score_after)) if (i and i.score_after is not None) else "—",
        ))
    errs = []
    for row in rows:
        if row.get("signin") and row["signin"].msg and not row["signin"].ok:
            errs.append("- **%s 签到**：%s" % (row["tag"], row["signin"].msg.replace("\n", " ")))
        if row.get("interact") and row["interact"].err:
            errs.append("- **%s 互动**：%s" % (row["tag"], row["interact"].err))
    if errs:
        lines += ["", "### 需要关注", ""] + errs
    lines += ["", "北京时间 %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))]
    try:
        with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log("写入 Step Summary 失败: %s" % e)


def notify(title: str, body: str) -> None:
    bark = os.environ.get("NOTIFY_BARK", "").strip()
    if bark:
        try:
            import urllib.request
            urllib.request.urlopen("%s/%s/%s" % (bark.rstrip("/"), urllib.parse.quote(title),
                                                 urllib.parse.quote(body)), timeout=10).read()
        except Exception as e:
            log("Bark 推送失败: %s" % e)
    sct = os.environ.get("NOTIFY_SERVERCHAN", "").strip()
    if sct:
        try:
            import urllib.request
            data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
            urllib.request.urlopen(urllib.request.Request("https://sctapi.ftqq.com/%s.send" % sct, data=data),
                                   timeout=10).read()
        except Exception as e:
            log("Server酱 推送失败: %s" % e)


# ============================================================================
# 主入口
# ============================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="ZEEHO 极核每日自动化：先签到，后互动")
    ap.add_argument("--token", help="单个 Token（优先于环境变量）")
    ap.add_argument("--token-file", help="Token 列表文件，每行一个")
    ap.add_argument("--signin-only", action="store_true", help="只签到")
    ap.add_argument("--interact-only", action="store_true", help="只互动")
    ap.add_argument("--status-only", action="store_true", help="只查状态，不执行")
    ap.add_argument("--dry-run", action="store_true", help="打印请求详情，不真正发送")
    ap.add_argument("--steps", default="", help="逗号分隔: publish,like,share,delete")
    ap.add_argument("--content", default="", help="发帖内容")
    ap.add_argument("--keep", action="store_true", help="发完帖不删除")
    ap.add_argument("--post-id", default="", help="指定要点赞的动态 ID，跳过发帖与查询")
    ap.add_argument("--community", action="store_true", help="自己没动态时允许赞公共流")
    ap.add_argument("--resolve-uid", action="store_true", help="用现有 token 自助获取 userId")
    ap.add_argument("--decode-url", default="", help="粘贴动态分享链接，解出 postId")
    ap.add_argument("--spread-seconds", type=int, default=0, help="多账号随机错峰上限(秒)")
    a = ap.parse_args()

    if a.decode_url:
        pid = post_id_from_url(a.decode_url)
        log("解出的 postId = %s" % pid if pid else "未能从链接中解出 postId")
        return 0 if pid else 1

    if a.resolve_uid:
        raw = a.token or os.environ.get("ZEEHO_TOKENS", "")
        if not raw:
            log("需要 --token <你的token>")
            return 1
        tk = clean_token(re.split(r"[\n,@]+", raw)[0])
        return 0 if resolve_uid(tk) else 1

    # ---- 取账号 ----
    raw = a.token or os.environ.get("ZEEHO_TOKENS", "")
    if not raw and a.token_file and os.path.isfile(a.token_file):
        raw = open(a.token_file, "r", encoding="utf-8").read()
        log("Token 来源: 文件 %s" % a.token_file)
    elif a.token:
        log("Token 来源: 命令行参数")
    elif os.environ.get("ZEEHO_TOKENS", "").strip():
        log("Token 来源: 环境变量 ZEEHO_TOKENS")
    accs = parse_accounts(raw)
    if not accs:
        log("未配置 Token。请设置环境变量 ZEEHO_TOKENS，或用 --token / --token-file 传入。")
        return 1

    # ---- 互动步骤 ----
    raw_pids = a.post_id or os.environ.get("ZEEHO_POST_ID", "")
    pids = parse_post_ids(raw_pids, len(accs))
    raw_steps = (a.steps or os.environ.get("INTERACT_STEPS", "")).strip()
    if raw_steps:
        steps = tuple(s.strip() for s in raw_steps.split(",") if s.strip() in ALL_STEPS)
    elif any(pids):
        steps = ("like",)
    else:
        steps = ALL_STEPS
    if a.keep and "delete" in steps:
        steps = tuple(s for s in steps if s != "delete")
    content = a.content or os.environ.get("ZEEHO_POST_CONTENT", "") or DEFAULT_CONTENT
    allow_community = a.community or os.environ.get("ENABLE_COMMUNITY_LIKE", "") in ("1", "true", "yes")

    do_signin_phase = not a.interact_only
    do_interact_phase = not a.signin_only

    log("共 %d 个账号 | 签到:%s 互动:%s" % (
        len(accs), "开" if do_signin_phase else "关", "开" if do_interact_phase else "关"))
    if do_interact_phase:
        log("互动步骤: %s | 发帖内容: %s | 删临时帖: %s" % (
            ", ".join(steps), content, "是" if "delete" in steps else "否"))

    if a.dry_run:
        for i, x in enumerate(accs, 1):
            log("  #%d token=%s userId=%s postId=%s"
                % (i, mask_token(x.token), x.userId or "(自动解析)", pids[i - 1] or "(自动查找)"))
        return 0

    # ---- 逐账号执行：先签到，后互动 ----
    rows: List[Dict[str, Any]] = []
    for i, acc in enumerate(accs, 1):
        log("")
        log("=" * 72)
        log("▶ %s  token=%s  userId=%s%s" % (
            acc.tag, mask_token(acc.token), acc.userId or "未提供(自动解析)",
            ("  postId=%s" % pids[i - 1]) if pids[i - 1] else ""))

        sr = do_signin(acc, status_only=a.status_only, dry_run=False) if do_signin_phase else None
        if sr:
            extra = []
            if sr.days is not None:
                extra.append("连签 %s 天" % sr.days)
            if not a.status_only and sr.ok:
                cal = fetch_calendar(acc.token)
                if cal:
                    sr.today_score, sr.month_signed = cal.get("today_score"), cal.get("month_signed")
            if sr.today_score not in (None, ""):
                extra.append("今日 +%s 积分" % sr.today_score)
            if sr.month_signed is not None:
                extra.append("本月已签 %s 天" % sr.month_signed)
            log("  [签到] %s%s" % (sr.msg, ("（%s）" % " · ".join(extra)) if extra else ""))

        ir = None
        if do_interact_phase:
            # 签到遇到 Token 失效时，互动必然也失败，直接跳过省请求
            if sr and sr.token_expired:
                log("  [互动] 跳过：签到已提示 Token 失效，不再重复请求")
                ir = InteractResult(err="Token 失效，未执行互动")
            else:
                ir = do_interact(acc, steps, allow_community,
                                 forced_post=pids[i - 1] or None, content=content)
                for d in ir.detail:
                    log("    " + d)
                if ir.score_after is not None:
                    log("    积分: %s -> %s%s" % (ir.score, ir.score_after,
                                                 (" (+%d)" % ir.gain) if ir.gain else ""))
                if ir.err:
                    log("    ⚠️ " + ir.err)

        rows.append({"tag": (sr.tag if sr and sr.tag != acc.tag else acc.tag),
                     "signin": sr, "interact": ir})

        if i < len(accs):
            wait = random.randint(5, 12) + (random.randint(0, a.spread_seconds) if a.spread_seconds else 0)
            log("  ⏳ 账号间等待 %d 秒" % wait)
            time.sleep(wait)

    # ---- 汇总 ----
    sign_ok = sum(1 for r in rows if r["signin"] and r["signin"].ok)
    sign_total = sum(1 for r in rows if r["signin"])
    inter_ok = sum(1 for r in rows if r["interact"] and any(
        [r["interact"].published, r["interact"].liked, r["interact"].shared]))

    log("")
    log("=" * 72)
    log("完成：签到 %d/%d · 互动有产出 %d/%d" % (sign_ok, sign_total, inter_ok, len(accs)))
    for r in rows:
        s, i2 = r["signin"], r["interact"]
        parts = []
        if s:
            parts.append("签到 %s" % ("✅" if s.ok else "❌"))
        if i2:
            parts.append("发帖%s 点赞%s 分享%s" % ("✓" if i2.published else "×",
                                                "✓" if i2.liked else "×",
                                                "✓" if i2.shared else "×"))
        log("  %s: %s" % (r["tag"], " · ".join(parts) or "未执行"))

    write_summary(rows)

    body = []
    for r in rows:
        s, i2 = r["signin"], r["interact"]
        line = r["tag"] + ": "
        seg = []
        if s:
            seg.append("签到 " + ("成功" if s.ok else "失败"))
            if s.days is not None:
                seg.append("连签%s天" % s.days)
        if i2:
            seg.append("发帖%s 点赞%s 分享%s" % ("√" if i2.published else "×",
                                              "√" if i2.liked else "×",
                                              "√" if i2.shared else "×"))
        body.append(line + " ".join(seg))
    notify("ZEEHO 极核 · 每日自动化", "\n".join(body))

    bad = (sign_total and sign_ok < sign_total) or (inter_ok < len(accs) if do_interact_phase else False)
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
