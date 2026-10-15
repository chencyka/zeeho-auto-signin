# -*- coding: utf-8 -*-
"""
ZEEHO 极核 · 每日点赞自动化
============================

只做「每日点赞」这一项互动任务（+1 积分），不发帖、不评论、不分享。

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
ZEEHO_TOKENS   : 多账号用 "@" 分隔, 每条形如  <token>:<userId>
                 例: aaaa-bbbb-cccc:1234567@dddd-eeee-ffff:7654321
                 若不带 ":userId", 则尝试从 ZEEHO_USER_IDS 按序取值
ZEEHO_USER_IDS : 可选, 逗号分隔的 userId 兜底列表
ENABLE_COMMUNITY_LIKE : 设为 1 时, 自己没有帖子才会去赞公共流帖（默认关闭）

用法
----
    python zeeho_like.py --dry-run                 # 不联网, 自检
    python zeeho_like.py --status-only             # 只查状态, 不点赞
    python zeeho_like.py --token <tk> --user-id <uid>
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
def sign_str(query_str, body_str):
    nonce = str(uuid.uuid4())
    ts = str(int(time.time() * 1000))
    param = "appId=%s&nonce=%s&timestamp=%s" % (APP_ID, nonce, ts)
    raw = query_str + body_str + param + APP_SECRET
    sg = hashlib.md5(hashlib.sha1(raw.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    return sg, param, ts, nonce


def request(method, path, token, uid, params=None, body=None):
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    query_str = "&".join("%s=%s" % (k, v) for k, v in params.items())
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    sg, param, ts, nonce = sign_str(query_str, body_str)

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
    """查积分余额；没有 userId 时跳过（不影响点赞本身）"""
    if not uid:
        return None
    st, d = request("GET", P_SETTING.format(uid=uid), token, uid)
    if st == 200 and isinstance(d, dict) and isinstance(d.get("data"), dict):
        return d["data"].get("score")
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


def do_like(acc, status_only=False, allow_community=False, auto_uid=True):
    tk = acc["token"]
    uid = acc["userId"] or ""
    res = {"ok": False, "liked": False, "postId": "", "score": None,
           "uid": uid, "detail": [], "err": ""}

    # userId 不是硬要求：先尽力自动补，补不到也能继续跑（服务端按 token 识别）
    if not uid and auto_uid:
        uid = autofill_uid(tk)
        res["uid"] = uid

    res["score"] = fetch_score(tk, uid)
    if status_only:
        res["ok"] = True
        return res

    # 1) 找自己的帖子 —— 没有 userId 时不传该参数，服务端据 token 返回本人动态
    post = None
    p = {"page": 1, "pageSize": 10}
    if uid:
        p["userId"] = uid
    st, d = request("GET", P_MINE_ARTICLES, tk, uid, p)
    if st == 200 and d.get("code") == OK_CODE:
        post = pick_post_id(d.get("data"), uid)
    res["detail"].append("查询本人动态: HTTP %s, postId=%s%s"
                         % (st, post or "无", "" if uid else "（未提供 userId，按 token 识别）"))

    # 2) 兜底: 公共流（默认关闭）—— 挑一条热门帖，通常是官方/车主内容
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
        res["detail"].append("查询公共动态: %s" % (post or "无"))

    if not post:
        res["ok"] = True
        res["err"] = ("自己的动态列表是空的。" if uid
                      else "取不到 userId，且无法按 token 识别到本人动态。")
        res["err"] += (" 想在这种情况也拿分，请把 Secret ENABLE_COMMUNITY_LIKE 设为 1"
                       "（会去赞社区推荐流里的热门内容），或先手动发过一条动态。")
        return res

    # 3) 点赞
    st, d = request("POST", P_LIKE, tk, uid, None, {"postId": str(post), "kindFlag": "0"})
    code = str(d.get("code", ""))
    msg = d.get("message", "")
    res["postId"] = post
    res["detail"].append("点赞 %s: HTTP %s, code=%s, %s" % (post, st, code, msg))
    if st == 200 and code == OK_CODE:
        res["liked"] = True
        res["ok"] = True
    elif code in ("30121", "30124", "30125") or "permit" in str(msg):
        res["err"] = "签名被拒(%s) —— App 端密钥可能已轮换" % code
    elif st == 401 or code == "40001":
        res["err"] = "Token 失效(%s)，请重新抓取" % (code or st)
    else:
        res["ok"] = True
        res["err"] = "点赞未成功: %s %s" % (code, msg)

    # 4) 事后积分（对比）；没 userId 时跳过
    after = fetch_score(tk, uid)
    if after is not None:
        res["score_after"] = after
        if isinstance(after, int) and isinstance(res["score"], int):
            res["gain"] = after - res["score"]
    return res


def write_summary(rows):
    if not SUMMARY_FILE:
        return
    lines = ["## 🎯 ZEEHO 极核 · 每日点赞", "",
             "| 账号 | userId | 结果 | 点赞对象 | 积分变化 |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s |" % (
            r["name"], r.get("uid") or "自动",
            ("✅ 已点赞" if r.get("liked") else ("⚠️ " + (r.get("err") or "跳过"))),
            r.get("postId") or "—",
            ("%s → %s" % (r.get("score"), r.get("score_after"))) if r.get("score_after") is not None else "—"))
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
    a = ap.parse_args()

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

    if a.dry_run:
        for i, x in enumerate(valid, 1):
            log("  #%d token=%s userId=%s" % (i, mask(x["token"]), x["userId"] or "(未提供, 运行时自动解析)"))
        log("✅ 配置可用。userId 不再是必填项 —— 缺失时脚本会先从 H5 端用户信息接口解析，"
            "再退化为按 token 识别本人动态。")
        return 0

    rows = []
    for i, x in enumerate(valid, 1):
        name = "账号%d (%s)" % (i, mask(x["token"]))
        log("\n" + "=" * 70)
        log("▶ %s  userId=%s" % (name, x["userId"] or "未提供（将自动尝试解析）"))
        r = do_like(x, a.status_only, allow_community)
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
    ok = sum(1 for r in rows if r.get("liked"))
    log("\n" + "=" * 70)
    log("完成: 点赞成功 %d / %d" % (ok, len(rows)))
    notify("极核点赞 %d/%d" % (ok, len(rows)),
           "\n".join("%s: %s" % (r["name"], "已点赞" if r.get("liked") else (r.get("err") or "跳过")) for r in rows))
    return 0 if ok or a.status_only else 2


if __name__ == "__main__":
    sys.exit(main())
