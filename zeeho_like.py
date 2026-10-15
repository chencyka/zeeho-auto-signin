# -*- coding: utf-8 -*-
"""
ZEEHO 极核 · 每日互动任务（发帖 + 点赞 + 分享）
==============================================

一天最多 3 分：发帖 +1、点赞 +1、分享 +1。
流程移植自 ZEEHO助手.apk 的 TaskRunner.autoPublish：

    发帖 → 点赞 → 分享 → 领分享积分 → 删除该帖

发的是**临时帖**，拿完分就删掉，不在主页留内容。
删除带硬保护：只有本次自己创建的才会被删，ZEEHO_POST_ID 指定的既有动态绝不删。

签到由 zeeho_signin.py 单独负责，两者互不影响。

与签到脚本的差异（关键）
------------------------
| 项        | 签到 zeeho_signin.py        | 点赞 zeeho_like.py              |
|-----------|-----------------------------|---------------------------------|
| 域名      | h5.zeehoev.com（H5 端）     | tapi.zeehoev.com（App 端）      |
| appId     | AiTXmBrm                    | S7qPWPU1                        |
| appSecret | 70c2c7458ab88ca9504ad...    | c5e0da7f4da28df805694ec3dd1...  |
| 头        | 同                          | 多 user_id / interfaceversion   |

签名算法（两端一致，已实测验证）:
    param = "appId=<APP_ID>&nonce=<uuid>&timestamp=<ms>"
    raw   = <查询串> + <请求体> + param + <APP_SECRET>
    sign  = md5( sha1(raw) 的 hex )
  * 查询串不带 "?", 只拼非空参数, 不 URL 编码
  * 请求体必须是紧凑 JSON (无空格); GET / 无 body 时为空串
  * 与 App 端原文脚本不同: 这里 body **参与**签名, 否则返回 30121 permit error

验证方式（无需真 token）:
    返回 401 {"error":"invalid_token"} -> 路径存在且签名通过
    返回 430 {"code":"30121"} / 30124  -> 签名或密钥错误

配置
----
ZEEHO_TOKENS       : 多账号用 "@" 分隔, 每条形如 <token>:<userId>
                     例: aaaa-bbbb-cccc:1234567@dddd-eeee-ffff:7654321
                     不带 ":userId" 也完全可以, 脚本会自动解析
INTERACT_STEPS     : 可选, 逗号分隔 publish,like,share,delete
                     不填默认全做; 填了 ZEEHO_POST_ID 时自动降为只点赞
ZEEHO_POST_ID      : 可选, 已有动态 ID 或整条分享链接(自动解 Base64)
ZEEHO_POST_CONTENT : 可选, 临时帖内容, 默认 "开心的一天"
ZEEHO_USER_IDS     : 可选, 逗号分隔的 userId 兜底列表
ENABLE_COMMUNITY_LIKE : 设为 1 时, 拿不到动态才会去赞公共流帖（默认关闭）

用法
----
    python zeeho_like.py --dry-run                 # 不联网, 自检
    python zeeho_like.py --status-only             # 只查积分, 不操作
    python zeeho_like.py --token <tk> --user-id <uid>
    python zeeho_like.py --steps publish,like      # 只发帖+点赞, 保留不删
    python zeeho_like.py --content "今天天气不错"    # 自定义发帖内容
    python zeeho_like.py --spread-seconds 600      # 多账号随机错峰上限(秒)
"""
import os, re, sys, ssl, time, json, uuid, random, hashlib, argparse, http.client, urllib.parse

# ---------------- 配置 ----------------
HOST = "tapi.zeehoev.com"
APP_ID = "S7qPWPU1"
APP_SECRET = "c5e0da7f4da28df805694ec3dd1fc6792e9df99d"
UA = "ZEEHO/5.0 (iPhone; iOS 17.0; Scale/3.00)"
OK_CODE = "10000"

P_MINE_ARTICLES = "/v1.0/social/cfmotoserversocial/community/mineArticleInfo"
P_COMMUNITY = "/v1.0/social/cfmotoserversocial/community/qbTzInfoNewV2"
P_LIKE = "/v1.0/social/cfmotoserversocial/socialCommu/likeFavoriteInfo"
P_SETTING = "/v1.0/mine/cfmotoservermine/setting/{uid}"
# 积分余额：这条**不需要 userId**，优先用它
P_TOTAL_INTEGRAL = "/v1.0/mine/cfmotoservermine/integral/totalIntegral"

# —— 完整互动任务（移植自 ZEEHO助手.apk 的 ApiClient / TaskRunner）——
P_PUBLISH = "/v1.0/social/cfmotoserversocial/commonArticle"
P_SHARE = "/v1.0/social/cfmotoserversocial/article/share/{pid}"
P_DELETE = "/v1.0/social/cfmotoserversocial/commonArticle/deleteArticle"
P_ADJUST_SHARE = "/v1.0/mine/cfmotoservermine/integral/adjustByShare"

DEFAULT_CONTENT = "开心的一天"
ALL_STEPS = ("publish", "like", "share", "delete")

TIMEOUT = 20
MAX_RETRY = 2
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY", "")


def log(m):
    print(m, flush=True)


def mask(t):
    t = str(t or "")
    return (t[:8] + "…" + t[-6:]) if len(t) > 18 else t


# ---------------- 签名与请求 ----------------
def app_nonce(ts):
    """
    App 端 nonce 不是 UUID，而是 <毫秒时间戳> + 16 位随机字母数字。
    （H5 端才是 UUID —— 见 SignUtil.sign 里的 "h5".equals 分支）
    """
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "%s%s" % (ts, "".join(random.choice(chars) for _ in range(16)))


def to_query(params):
    """
    照 SignUtil.toQuery: 只跳过 None，然后**按 key 字典序排序**再拼。
    顺序不同会导致签名不一致 —— 这是最容易踩的坑。
    """
    keys = sorted([k for k, v in params.items() if v is not None])
    return "&".join("%s=%s" % (k, params[k]) for k in keys)


def sign_str(query_str, body_str, method="GET"):
    ts = str(int(time.time() * 1000))
    nonce = app_nonce(ts)
    # DELETE 时 body 强制为空（与 SignUtil 一致）
    eff_body = "" if method == "DELETE" else body_str
    param = "appId=%s&nonce=%s&timestamp=%s" % (APP_ID, nonce, ts)
    raw = query_str + eff_body + param + APP_SECRET
    sg = hashlib.md5(hashlib.sha1(raw.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    return sg, param, ts, nonce


def request(method, path, token, uid, params=None, body=None):
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    query_str = to_query(params)
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    sg, param, ts, nonce = sign_str(query_str, body_str, method)

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept-Language": "zh-CN",
        "Authorization": "Bearer " + token,
        "User-Agent": UA,
        "user_id": str(uid or ""),
        "interfaceversion": "2",
        "cfmoto-x-param": param,
        "cfmoto-x-sign": sg,
        "cfmoto-x-sign-type": "0",
        "timestamp": ts,
        "nonce": nonce,
        "signature": sg,
    }
    url = path + (("?" + query_str) if query_str else "")
    payload = body_str.encode("utf-8") if (method != "GET" and body_str) else None

    last = None
    for attempt in range(MAX_RETRY + 1):
        try:
            conn = http.client.HTTPSConnection(HOST, timeout=TIMEOUT, context=CTX)
            conn.request(method, url, body=payload, headers=headers)
            r = conn.getresponse()
            txt = r.read().decode("utf-8", "ignore")
            conn.close()
            return r.status, parse_body(txt)
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    return -1, {"_err": str(last), "code": "-1", "message": "网络异常"}


def parse_body(txt):
    try:
        return json.loads(txt)
    except Exception:
        pass
    # 鉴权层返回的是 XML：<InvalidTokenException><error>invalid_token</error>…
    if "invalid_token" in txt:
        return {"code": "401", "message": "invalid_token（Token 无效或已过期）"}
    return {"_raw": txt, "code": "-2", "message": "非 JSON 响应"}


def pick_post_id(data, uid=""):
    """从 mineArticleInfo / qbTzInfoNewV2 里挑一个属于本人的帖子 ID"""
    rows = []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for k in ("records", "list", "rows", "data"):
            v = data.get(k)
            if isinstance(v, list):
                rows = v
                break
        if rows == [] and isinstance(data.get("data"), dict):
            rows = pick_rows(data["data"])
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


def pick_rows(d):
    for k in ("records", "list", "rows"):
        v = d.get(k)
        if isinstance(v, list):
            return v
    return []


def extract_id(item):
    if not isinstance(item, dict):
        return None
    for k in ("postId", "postid", "articleId", "articleID", "uuid", "tuuid", "id", "dataId", "tid"):
        v = item.get(k)
        if v not in (None, "", 0):
            return str(v)
    return None


# ---------------- 账号 ----------------
def parse_accounts(raw_tokens, raw_uids):
    """
    支持两种写法:
      1) "<token>:<userId>@<token2>:<userId2>"
      2) "token1@token2"  +  ZEEHO_USER_IDS="uid1,uid2"
    """
    uids = [x.strip() for x in re.split(r"[,;\s]+", raw_uids or "") if x.strip()]
    out = []
    for i, chunk in enumerate([c.strip() for c in (raw_tokens or "").split("@") if c.strip()]):
        chunk = chunk.strip().strip('"').strip("'")
        chunk = re.sub(r"^[bB]earer\s+", "", chunk).strip()
        if ":" in chunk and re.search(r":\d+\s*$", chunk):
            tk, _, uid = chunk.rpartition(":")
        else:
            tk = chunk
            uid = uids[i] if i < len(uids) else ""
        out.append({"token": tk.strip(), "userId": uid.strip()})
    return out


# ---------------- H5 端（用于自动解析 userId）----------------
H5_HOST = "h5.zeehoev.com"
H5_APP_ID = "AiTXmBrm"
H5_APP_SECRET = "70c2c7458ab88ca9504ad0521f170075bc91f2f7"
H5_UA = "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"


def h5_sign(query_str, body_str=""):
    nonce = str(uuid.uuid4())
    ts = str(int(time.time() * 1000))
    param = "appId=%s&nonce=%s&timestamp=%s" % (H5_APP_ID, nonce, ts)
    raw = query_str + body_str + param + H5_APP_SECRET
    sg = hashlib.md5(hashlib.sha1(raw.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    return sg, param, ts, nonce


def h5_request(path, token, params=None):
    """签发到时复用的 H5 客户端；同一个 token 在 H5 端也能用。"""
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    query_str = "&".join("%s=%s" % (k, v) for k, v in params.items())
    sg, param, ts, nonce = h5_sign(query_str)
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://" + H5_HOST,
        "Referer": "https://%s/activity/signin" % H5_HOST,
        "User-Agent": H5_UA,
        "Authorization": "Bearer " + token,
        "Cfmoto-X-Sign": sg,
        "Cfmoto-X-Sign-Type": "0",
        "Cfmoto-X-Param": param,
        "timestamp": ts,
        "nonce": nonce,
    }
    url = path + (("?" + query_str) if query_str else "")
    try:
        conn = http.client.HTTPSConnection(H5_HOST, timeout=TIMEOUT, context=CTX)
        conn.request("GET", url, headers=headers)
        r = conn.getresponse()
        txt = r.read().decode("utf-8", "ignore")
        conn.close()
        return r.status, parse_body(txt)
    except Exception as e:
        return -1, {"code": "-1", "message": str(e)}


DIGITS = r"\d{6,20}"
UID_KEYS = ("userId", "id", "uid", "memberId", "accountId", "custId", "userNo")


def deep_find_uid(obj, depth=0):
    """递归找疑似 userId 的数字串"""
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


def autofill_uid(token):
    """
    自动补 userId：同一个 token 在 H5 端调一下用户信息接口，返回体里几乎必然带数字 id。
    这样就不用手工抓包了。拿不到则返回 ""，调用方继续走「不带 userId」的降级路径。
    """
    for p in ("/cfmotoservermine/H5/baseInfo", "/cfmotoservermine/baseInfo"):
        st, d = h5_request(p, token)
        if st != 200:
            continue
        uid = deep_find_uid(d)
        if uid:
            log("   🔎 自动取到 userId=%s（来源 %s）" % (uid, p))
            return uid
    return ""


def fetch_score(token, uid):
    """
    查积分余额。优先走 /integral/totalIntegral（不依赖 userId），
    失败再退到 /setting/{userId}（需要 userId，没有就返回 None）。
    """
    st, d = request("GET", P_TOTAL_INTEGRAL, token, uid)
    if st == 200 and isinstance(d, dict) and d.get("code") == OK_CODE:
        v = d.get("data")
        if isinstance(v, dict):
            for k in ("score", "totalIntegral", "integral", "usableIntegral"):
                if isinstance(v.get(k), int):
                    return v[k]
        elif isinstance(v, int):
            return v
    if uid:
        st2, d2 = request("GET", P_SETTING.format(uid=uid), token, uid)
        if st2 == 200 and isinstance(d2, dict) and isinstance(d2.get("data"), dict):
            s = d2["data"].get("score")
            if isinstance(s, int):
                return s
    return None


def resolve_uid(token):
    """
    自助获取 userId：优先走 H5 端（token 通用、无需抓包），再试 App 端若干接口。
    这些接口都需要有效 token；用真 token 跑一次即可拿到数字 userId。
    """
    uid = autofill_uid(token)
    if uid:
        print("\n请把下面这组配置填进 GitHub Secrets：")
        print("    ZEEHO_TOKENS = %s:%s" % (token, uid))
        return uid

    print("  ※ H5 端未返回 id，继续尝试 App 端…\n")
    candidates = [
        ("GET", "/v1.0/mine/cfmotoservermine/setting", {}),
        ("GET", "/v1.0/mine/cfmotoservermine/setting/", {}),
        ("GET", "/v1.0/mine/cfmotoservermine/userInfo", {}),
        ("GET", "/v1.0/mine/cfmotoservermine/baseInfo", {}),
        ("GET", "/v1.0/mine/cfmotoservermine/user/info", {}),
        ("GET", "/v1.0/mine/cfmotoservermine/integral/totalIntegral", {}),
        ("GET", "/v1.0/social/cfmotoserversocial/community/mineArticleInfo",
         {"page": 1, "pageSize": 10}),
    ]
    print("用 token %s 尝试获取 userId…\n" % mask(token))
    for m, p, q in candidates:
        st, d = request(m, p, token, "", q)
        uid = deep_find_uid(d) if isinstance(d, dict) else None
        print("  %-58s HTTP %-4s %s" % (p, st, ("✅ userId=" + uid) if uid else str(d.get("message", d))[:60]))
        if uid:
            print("\n请把下面这组配置填进 GitHub Secrets：")
            print("    ZEEHO_TOKENS = %s:%s" % (token, uid))
            return uid
    print("\n未能自动取到 userId。请用手机 App 抓包：")
    print("  MitM 监听 tapi.zeehoev.com，打开「我的」页，")
    print("  请求 URL /v1.0/mine/cfmotoservermine/setting/<数字> 里那段数字即 userId。")
    return None


def parse_post_ids(raw, n):
    """
    解析 ZEEHO_POST_ID。
    支持 @ 分隔按账号顺序对应；若只给一个值则所有账号共用。
    """
    parts = [x.strip() for x in (raw or "").split("@") if x.strip()]
    if not parts:
        return [""] * n
    if len(parts) == 1:
        return parts * n
    return [parts[i] if i < len(parts) else "" for i in range(n)]


def b64_decode(s):
    """宽容的 Base64 解码（自动补齐 padding）"""
    try:
        import base64
        s = s.strip()
        return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "ignore")
    except Exception:
        return ""


def post_id_from_url(url):
    """
    从动态分享链接里解出 postId。
    链接形态: https://h5.zeehoev.com/post?time=1792056147099&uuid=MjAyNjEwMTUwMDg4MDkzMTU=
             其中 uuid 是 postId 的 Base64
    """
    import urllib.parse as up
    m = re.search(r"[?&]uuid=([^&#]+)", url)
    if m:
        dec = b64_decode(up.unquote(m.group(1)))
        if dec.isdigit():
            return dec
    m = re.search(r"\b(\d{15,20})\b", url)  # 已经是明文数字
    if m:
        return m.group(1)
    return ""


def api_publish(tk, uid, content):
    """发帖。body 结构照搬 APK：{postSubInfo:{topicList:[]}, topicid:"", postcontent:...}"""
    body = {"postSubInfo": {"topicList": []}, "topicid": "", "postcontent": content}
    st, d = request("POST", P_PUBLISH, tk, uid, None, body)
    ok = st == 200 and str(d.get("code", "")) == OK_CODE
    if ok:
        # 发布响应里通常直接带 id；取不到就返回 None 让调用方去查列表
        pid = extract_id(d.get("data"))
        return ok, pid, ""
    return False, None, str(d.get("message", "HTTP %s" % st))


def api_share(tk, uid, post_id):
    st, d = request("PUT", P_SHARE.format(pid=post_id), tk, uid)
    ok = st == 200 and str(d.get("code", "")) == OK_CODE
    return ok, str(d.get("message", "")) if not ok else ""


def api_delete(tk, uid, post_id):
    st, d = request("DELETE", P_DELETE, tk, uid, {"articleId": post_id, "postType": "1"})
    ok = st == 200 and str(d.get("code", "")) == OK_CODE
    return ok, str(d.get("message", "")) if not ok else ""


def api_adjust_share(tk, uid):
    """领取分享积分（每次分享后调一次，服务端自己按天去重）"""
    st, d = request("GET", P_ADJUST_SHARE, tk, uid)
    return st == 200 and str(d.get("code", "")) == OK_CODE, str(d.get("message", ""))


def api_like(tk, uid, post_id):
    return request("POST", P_LIKE, tk, uid, None, {"postId": str(post_id), "kindFlag": "0"})


def do_interact(acc, steps, allow_community=False, auto_uid=True, forced_post=None,
                content=DEFAULT_CONTENT, gap=(2, 4), status_only=False):
    tk = acc["token"]
    uid = acc["userId"] or ""
    res = {"ok": True, "published": False, "liked": False, "shared": False,
           "deleted": False, "postId": "", "score": None,
           "uid": uid, "detail": [], "err": ""}

    def nap():
        time.sleep(random.randint(gap[0], gap[1]))

    # userId 不是硬要求：先尽力自动补，补不到也能继续跑（服务端按 token 识别）
    if not uid and auto_uid:
        uid = autofill_uid(tk)
        res["uid"] = uid

    res["score"] = fetch_score(tk, uid)
    if status_only:
        return res

    post = forced_post or None
    # 只有本次自己创建的动态才允许删除 —— 绝不能删用户已有的内容
    self_published = False

    # ---------- 1) 发帖 ----------
    if "publish" in steps and not post:
        ok, pid, msg = api_publish(tk, uid, content)
        if ok:
            self_published = True
            res["published"] = True
            post = pid
            res["detail"].append("[发帖] 内容=%s%s" % (content, (" id=%s" % pid) if pid else ""))
        else:
            res["detail"].append("[发帖] 失败: %s" % msg)
        nap()

    # ---------- 2) 确定 postId ----------
    if not post:
        p = {"page": 1, "pageSize": 10}
        if uid:
            p["userId"] = uid
        st, d = request("GET", P_MINE_ARTICLES, tk, uid, p)
        if st == 200 and d.get("code") == OK_CODE:
            post = pick_post_id(d.get("data"), uid)
        res["detail"].append("[查动态] postId=%s" % (post or "无"))

    # ---------- 3) 兜底：公共流（默认关闭）----------
    if not post and allow_community:
        st, d = request("GET", P_COMMUNITY, tk, uid,
                        {"page": 1, "pageSize": 20, "postModule": 2, "slidingType": 1})
        if st == 200 and d.get("code") == OK_CODE:
            rows = d.get("data") if isinstance(d.get("data"), list) else []
            hot = sorted([r for r in rows if isinstance(r, dict)],
                         key=lambda r: int(r.get("likeTimes") or 0), reverse=True)
            for r in hot:
                if uid and str(r.get("userId") or "") == str(uid):
                    continue
                pid = extract_id(r)
                if pid:
                    post = pid
                    break
        res["detail"].append("[公共动态] postId=%s" % (post or "无"))

    if not post:
        res["err"] = ("发不出动态，动态列表也是空的。" if uid
                      else "取不到 userId，且无法按 token 识别到本人动态。")
        res["err"] += (" 可设 ENABLE_COMMUNITY_LIKE=1 去赞推荐流热门内容，"
                       "或先在社区手动发一条动态。")
        return res

    res["postId"] = post

    # ---------- 4) 点赞 ----------
    if "like" in steps:
        st, d = api_like(tk, uid, post)
        code, msg = str(d.get("code", "")), d.get("message", "")
        res["liked"] = st == 200 and code == OK_CODE
        res["detail"].append("[点赞] %s -> %s" % (post, "成功" if res["liked"] else "%s %s" % (code, msg)))
        if not res["liked"] and (code in ("30121", "30124", "30125") or "permit" in str(msg)):
            res["err"] = "签名被拒(%s) —— App 端密钥可能已轮换" % code
        elif not res["liked"] and (st == 401 or code == "40001"):
            res["err"] = "Token 失效(%s)，请重新抓取" % (code or st)
        nap()

    # ---------- 5) 分享 + 领积分 ----------
    if "share" in steps:
        ok, msg = api_share(tk, uid, post)
        res["shared"] = ok
        res["detail"].append("[分享] -> %s" % ("成功" if ok else msg))
        nap()
        if ok:
            aok, amsg = api_adjust_share(tk, uid)
            res["detail"].append("[分享积分] -> %s" % ("已领取" if aok else amsg))

    # ---------- 6) 删除本次临时帖 ----------
    if "delete" in steps and self_published:
        nap()
        ok, msg = api_delete(tk, uid, post)
        res["deleted"] = ok
        res["detail"].append("[删帖] -> %s" % ("已删除" if ok else msg))
    elif "delete" in steps:
        res["detail"].append("[删帖] 跳过：目标是既有动态，不是本次创建的")

    # ---------- 7) 事后积分（对比）----------
    after = fetch_score(tk, uid)
    if after is not None:
        res["score_after"] = after
        if isinstance(after, int) and isinstance(res["score"], int):
            res["gain"] = after - res["score"]
    return res


def write_summary(rows):
    if not SUMMARY_FILE:
        return
    def tick(b):
        return "是" if b else "—"

    lines = ["## ZEEHO 极核 · 每日互动任务", "",
             "| 账号 | userId | 发帖 | 点赞 | 分享 | 删除 | 动态ID | 积分变化 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["name"], r.get("uid") or "自动",
            tick(r.get("published")), tick(r.get("liked")), tick(r.get("shared")),
            tick(r.get("deleted")),
            r.get("postId") or "—",
            ("%s → %s" % (r.get("score"), r.get("score_after"))) if r.get("score_after") is not None else "—"))
    for r in rows:
        if r.get("err"):
            lines.append("") 
            lines.append("- **%s**: %s" % (r["name"], r["err"]))
    lines += ["", "由 GitHub Actions 自动执行 · 北京时间 %s" % time.strftime("%Y-%m-%d %H:%M:%S",
              time.localtime(time.time() + 8 * 3600))]
    try:
        with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        log("[warn] 写 Summary 失败: %s" % e)


def notify(title, body):
    key = os.environ.get("NOTIFY_BARK", "").strip()
    if key:
        try:
            import urllib.request as u
            u.urlopen("https://api.day.app/%s/%s/%s" % (key, u.quote(title), u.quote(body)), timeout=10).read()
        except Exception as e:
            log("[warn] Bark 推送失败: %s" % e)
    sk = os.environ.get("NOTIFY_SERVERCHAN", "").strip()
    if sk:
        try:
            import urllib.request as u, urllib.parse as p
            data = p.urlencode({"title": title, "desp": body}).encode()
            u.urlopen(u.Request("https://sctapi.ftqq.com/%s.send" % sk, data=data), timeout=10).read()
        except Exception as e:
            log("[warn] Server酱 推送失败: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="")
    ap.add_argument("--user-id", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status-only", action="store_true")
    ap.add_argument("--spread-seconds", type=int, default=0)
    ap.add_argument("--community", action="store_true", help="自己没帖时允许赞公共流")
    ap.add_argument("--resolve-uid", action="store_true", help="用现有 token 自助获取 userId")
    ap.add_argument("--post-id", default="", help="直接指定要点赞的动态 ID，跳过发帖与查询")
    ap.add_argument("--steps", default="", help="逗号分隔: publish,like,share,delete")
    ap.add_argument("--content", default="", help="发帖内容")
    ap.add_argument("--keep", action="store_true", help="发完帖不删除（默认会删掉临时帖）")
    ap.add_argument("--decode-url", default="",
                    help="粘贴动态分享链接，自动解出 postId（如 https://h5.zeehoev.com/post?uuid=xxx）")
    a = ap.parse_args()

    if a.decode_url:
        pid = post_id_from_url(a.decode_url)
        log("解出的 postId = %s" % pid if pid else "未能从链接中解出 postId")
        return 0 if pid else 1

    if a.resolve_uid:
        tk = re.sub(r"^[bB]earer\s+", "", (a.token or os.environ.get("ZEEHO_TOKENS", "")).strip().split("@")[0].split(":")[0])
        if not tk:
            log("❌ 需要 --token <你的token>")
            return 1
        return 0 if resolve_uid(tk) else 1

    if a.token:
        accs = [{"token": re.sub(r"^[bB]earer\s+", "", a.token.strip()), "userId": a.user_id.strip()}]
    else:
        raw = os.environ.get("ZEEHO_TOKENS", "").strip()
        if not raw:
            log("❌ 未提供 ZEEHO_TOKENS（格式: <token>:<userId>，多账号用 @ 分隔）")
            return 1
        accs = parse_accounts(raw, os.environ.get("ZEEHO_USER_IDS", ""))

    allow_community = a.community or os.environ.get("ENABLE_COMMUNITY_LIKE", "") in ("1", "true", "yes")
    valid = [x for x in accs if x["token"]]
    log("共 %d 个账号" % len(valid))

    # 手动指定的 postId：命令行优先，其次环境变量
    raw_pids = a.post_id or os.environ.get("ZEEHO_POST_ID", "")
    if raw_pids:
        # 环境变量里允许直接贴整条分享链接
        for piece in re.split(r"[\s,]+", raw_pids.strip()):
            if piece.startswith("http"):
                raw_pids = raw_pids.replace(piece, post_id_from_url(piece) or piece)
    pids = parse_post_ids(raw_pids, len(valid))

    # 步骤：默认「发帖→点赞→分享→删除」整套（+3 分）；
    # 一旦手动指定了 postId，就只做点赞，不去动既有内容
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

    if a.dry_run:
        for i, x in enumerate(valid, 1):
            log("  #%d token=%s userId=%s postId=%s"
                % (i, mask(x["token"]), x["userId"] or "(自动解析)", pids[i - 1] or "(自动查找)"))
        log("  执行步骤: %s" % ", ".join(steps))
        log("  发帖内容: %s" % content)
        log("  删除临时帖: %s" % ("是" if "delete" in steps else "否"))
        return 0

    rows = []
    for i, x in enumerate(valid, 1):
        name = "账号%d (%s)" % (i, mask(x["token"]))
        log("\n" + "=" * 70)
        extra = "  postId=%s" % pids[i - 1] if pids[i - 1] else ""
        log("▶ %s  userId=%s%s" % (name, x["userId"] or "未提供（自动解析）", extra))
        r = do_interact(x, steps, allow_community, forced_post=pids[i - 1] or None,
                        content=content, status_only=a.status_only)
        for d in r["detail"]:
            log("   " + d)
        if r.get("score_after") is not None:
            log("   积分: %s -> %s %s" % (r.get("score"), r.get("score_after"),
                                        ("(+%d)" % r["gain"]) if r.get("gain") else ""))
        if r["err"]:
            log("   ⚠️ " + r["err"])
        rows.append(dict({"name": name, "userId": x["userId"]}, **r))
        if i < len(valid):
            wait = random.randint(3, 8) + (random.randint(0, a.spread_seconds) if a.spread_seconds else 0)
            log("   ⏳ 等待 %d 秒" % wait)
            time.sleep(wait)

    write_summary(rows)
    done = sum(1 for r in rows if any([r.get("published"), r.get("liked"), r.get("shared")]))
    log("\n" + "=" * 70)
    log("完成: 有产出的账号 %d / %d （步骤: %s）" % (done, len(rows), ", ".join(steps)))
    notify("极核互动 %d/%d" % (done, len(rows)),
           "\n".join("%s: 发帖%s 点赞%s 分享%s" % (
               r["name"], "√" if r.get("published") else "×",
               "√" if r.get("liked") else "×", "√" if r.get("shared") else "×")
               for r in rows))
    return 0 if done or a.status_only else 2


if __name__ == "__main__":
    sys.exit(main())
