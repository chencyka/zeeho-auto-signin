# -*- coding: utf-8 -*-
"""
极核社区(cfmotoserversocial)接口探测工具。

前置发现
--------
h5.zeehoev.com 是一个网关, 两种路由形态:

  A. /cfmotoserversocial/H5/xxx   -> H5 网关, 要 Cfmoto-X-Sign 签名
  B. /cfmotoserversocial/xxx      -> 直接转发到原生社区服务, 只校验 Bearer token

无论哪种, 鉴权都在最外层, 所以:
  * 用假 token 时, 两个形态分别返回 30124(签名层) / 401 invalid_token,
    且**任何子路径都如此** -> 无法区分路径是否存在。
  * 必须用**真实 token** 才能看出差别: 不存在的路径会返回 404 或
    {"code":"40000","message":"网络开小差了..."}, 存在的会返回业务码。

用法
----
  python probe_social.py <你的token>                    # 标准扫描
  python probe_social.py <你的token> --mode bearer      # 只验 Bearer(不带签名)
  python probe_social.py <你的token> --extra /路径A /路径B
"""
import sys, os, json, time, ssl, argparse, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zeeho_signin import build_headers, stringify_query, HOST, UA  # 复用已验证的签名与头

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

SERVICE = "/cfmotoserversocial"

L1 = [
    "post", "article", "comment", "topic", "circle", "community", "activityIcon",
    "survey", "praise", "like", "thumbUp", "favor", "collect", "moments", "feed",
    "activity", "user", "reply", "recommendPost", "postInfo", "social",
]

L2 = [
    "praise", "like", "thumbUp", "thumbup", "zan", "digg", "favor", "collect",
    "cancel", "cancelPraise", "praiseOrCancel", "cancelLike", "add", "addPraise",
    "save", "savePraise", "doPraise", "toggle", "operate", "remove", "delete",
    "create", "list", "info", "detail", "all", "page", "status", "count", "check",
]


def call(path, token, method="GET", params=None, body=None, bearer_only=False):
    query_str = stringify_query(params)
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""
    if bearer_only:
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
        }
    else:
        headers = build_headers(token, query_str, body_str)
    url = HOST + path + (("?" + query_str) if query_str else "")
    data = body_str.encode("utf-8") if method == "POST" else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return -1, str(e)


def verdict(code, txt):
    if code == 404:
        return "GONE"
    if code == 401 and "invalid_token" in txt:
        return "TOKEN_BAD"
    if "30124" in txt or "30125" in txt or "30121" in txt or "permit error" in txt:
        return "SIG_ERR"
    if "40000" in txt or "网络开小差" in txt:
        return "GONE_FALLBACK"
    return "HIT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("token")
    ap.add_argument("--mode", default="sign", choices=["sign", "bearer"])
    ap.add_argument("--extra", nargs="*", default=[])
    ap.add_argument("--delay", type=float, default=0.2)
    a = ap.parse_args()
    token = a.token.strip().strip('"').strip("'")
    bearer = a.mode == "bearer"

    # ---------- 对照组 ----------
    print("=" * 78)
    print("对照组: 确认 token 有效 + 签名正确")
    print("=" * 78)
    ctrl = [
        ("/cfmotoservermine/H5/baseInfo", "GET"),
        ("/cfmotoservermine/H5/signin", "GET"),
        (SERVICE + "/H5/circle/info", "GET"),
    ]
    token_ok = False
    for p, m in ctrl:
        c, t = call(p, token, m, {"circleId": "1"}, bearer_only=bearer)
        v = verdict(c, t)
        print("  %-52s %-5s %-14s" % (p, c, v))
        print("      %s" % t[:200].replace("\n", " "))
        if v == "HIT" or c == 200:
            token_ok = True
    print("\n  token/signature 可用性: %s" % ("OK" if token_ok else "未通过, 下面结果不可信"))

    # ---------- 扫描 ----------
    hits = []
    print("\n" + "=" * 78)
    print("扫描: GET")
    print("=" * 78)
    paths = []
    for s in L1:
        paths.append("%s/%s" % (SERVICE, s))
    for pre in L1:
        for act in L2:
            paths.append("%s/%s/%s" % (SERVICE, pre, act))
    paths += a.extra

    seen = set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        c, t = call(p, token, "GET", {"postId": "1", "commentId": "1", "articleId": "1", "id": "1"},
                    bearer_only=bearer)
        v = verdict(c, t)
        if v == "HIT":
            hits.append((p, "GET", c, t))
            print("  >> %-54s %-5s %s" % (p, c, v))
            print("        %s" % t[:220].replace("\n", " "))
        time.sleep(a.delay)
    print("  GET 完成, 命中 %d" % len(hits))

    print("\n" + "=" * 78)
    print("扫描: POST")
    print("=" * 78)
    body = {"postId": "1", "commentId": "1", "articleId": "1",
            "businessId": "1", "id": "1", "praiseType": 1, "type": 1}
    for p in list(seen):
        c, t = call(p, token, "POST", {}, body, bearer_only=bearer)
        v = verdict(c, t)
        if v == "HIT":
            hits.append((p, "POST", c, t))
            print("  >> %-54s %-5s %s" % (p, c, v))
            print("        %s" % t[:220].replace("\n", " "))
        time.sleep(a.delay)
    print("  POST 完成")

    print("\n" + "=" * 78)
    print("命中汇总 (%d)" % len(hits))
    print("=" * 78)
    uniq = {}
    for p, m, c, t in hits:
        uniq.setdefault(p, []).append((m, c, t))
    for p, lst in uniq.items():
        for m, c, t in lst:
            print("  %-6s %-52s %s" % (m, p, c))
            print("        %s" % t[:300].replace("\n", " "))
    json.dump([{"method": m, "path": p, "code": c, "body": t} for p, m, c, t in hits],
              open("social_hits.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if hits:
        print("\n已写 social_hits.json —— 把它发给助手即可据此写自动化脚本")
    else:
        print("\n无命中。建议: 手机上 mitmproxy/Charles 抓一次点赞请求, 把 URL/方法/body 给助手。")


if __name__ == "__main__":
    main()
