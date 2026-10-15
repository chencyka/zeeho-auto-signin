# ZEEHO 极核每日自动化（签到 + 点赞）

每天自动给极核（ZEEHO / 春风动力 CFMOTO）App 账号签到，跑在 **GitHub Actions** 上，无需服务器、无需手机常开。

签到流程移植自 [MonicaGmm/zeeho-quanx](https://github.com/MonicaGmm/zeeho-quanx)（Quantumult X 版），本仓库是其 Python 实现 + GitHub Actions 定时部署。

> **本项目与极核（ZEEHO）/ 春风动力（CFMOTO）无任何关联，也不是官方产品。**
> 仅供个人学习与自用。禁止用于商业用途、批量账号、灰产或任何违反极核用户协议的行为。
> Token 等同账号凭证，泄露会导致他人操控你的账号，请妥善保管。

---

## 一、快速开始

### 1. Fork 本仓库

点右上角 **Fork**。

### 2. 配置 Token

进入你 Fork 后的仓库：
**Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
| --- | --- |
| `ZEEHO_TOKENS` | 你的 Token，多账号用逗号或换行分隔 |

可选（推送通知，不配则只写日志和 Summary）：

| Name | Value |
| --- | --- |
| `NOTIFY_BARK` | Bark 地址，如 `https://api.day.app/你的key` |
| `NOTIFY_SERVERCHAN` | Server酱 SendKey |

### 3. 启用并手动测试

**Actions → ZEEHO 每日签到 → Run workflow**，先跑一次确认能通。

之后每天 **08:30（北京时间）** 自动执行。

---

## 二、Token 怎么抓

极核 App 已登录状态下，两种方式任选：

**方式 A：抓包（推荐，一次搞定）**

用 Stream / Charles / mitmproxy 抓极核 App 的任意业务请求，看请求头：

```
Authorization: Bearer xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

冒号后面那一整串（UUID 格式）就是 Token。

**方式 B：签到页地址**

打开这个页面，地址栏 `?token=` 后面那段：

```
https://h5.zeehoev.com/activity/signin?token=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

> ⚠️ 只要 UUID 那一段。**不要**把 `Cfmoto-X-Sign` / `appId` 当成 Token —— 那是接口签名参数，不是登录凭证。

**Token 会过期。** 过期后脚本会报"Token 失效"，重新抓一次替换 Secret 即可。

---

## 三、本地运行

```bash
# 只需要 Python 3.8+，无第三方依赖
export ZEEHO_TOKENS="uuid1,uuid2"

python zeeho_signin.py                  # 执行签到
python zeeho_signin.py --status-only    # 只查状态，不签到
python zeeho_signin.py --dry-run        # 打印请求详情，不真正发送
python zeeho_signin.py --token "uuid"   # 单个 Token 直接传
```

---

## 四、接口说明（踩坑记录， valuable ）

这些是逆向 + 实测得到的，改脚本前先看这里。

### 基础信息

| 项 | 值 |
| --- | --- |
| Host | `https://h5.zeehoev.com` |
| 鉴权 | `Authorization: Bearer <token>`（UUID） |
| 签名 | `Cfmoto-X-Sign = md5(sha1(v))` |

### 签名算法

```
v = 查询串 + 请求体 + "appId=<APP_ID>&nonce=<uuid>&timestamp=<毫秒>" + <APP_SECRET>
Cfmoto-X-Sign     = md5(sha1(v))
Cfmoto-X-Param    = appId=<APP_ID>&nonce=<nonce>&timestamp=<ts>
Cfmoto-X-Sign-Type = 0
```

注意：
- 查询串**不做 URL 编码**，就是 `k=v&k=v`，且只拼非空参数
- 请求体是 JSON 字符串；GET 请求时为空串
- `md5(sha1(...))` 是两层，sha1 输出 hex 字符串后再 md5

### 用到的 4 个接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/cfmotoservermine/baseInfo` | 用户信息（昵称、头像） |
| GET | `/cfmotoservermine/H5/signin` | 查签到状态（`data.lastTime` / `data.continueDays`） |
| POST | `/cfmotoservermine/H5/signin` | 执行签到 |
| GET | `/cfmotoservermine/H5/signin/info?month=YYYY-M` | 本月签到日历 |

### 三个容易踩的坑

**1. 签到接口返回码不可信**

新签成功和重复签都可能返回 `code: "10000"` 或 `"repeatedly_operation"`。
所以脚本不看 POST 的返回码下结论，一律**再 GET 一次**用 `data.lastTime` 回查真实状态。

**2. H5 取不到账户积分余额**

H5 全部 22 个接口里没有返回余额的。`integralScore` 是**当天签到获得**的积分（前端提示语是"恭喜获得N积分"），不是余额。余额由原生 App 自己的接口提供，用 H5 Token 取不到。

**3. 服务端会轮换密钥和路径**

- 2026-10-10：签到接口迁移到 `/cfmotoservermine/H5/` 前缀，同时换发 appId/appSecret。旧 `AppID=Sw5F9uJi` 和旧路径都会被网关拦成 `30125 permit error`。
- 当前值：`APP_ID = AiTXmBrm`，`APP_SECRET = 70c2c7458ab88ca9504ad0521f170075bc91f2f7`
- **如果所有账号都报 30125 / 30121 / permit error，就是又轮换了**，重新抓包换 `zeeho_signin.py` 顶部的两个常量。签名算法本身（`md5(sha1(...))`）一直没变。

---

## 五、每日点赞（独立任务）

极核的互动任务是分开计分的：**发帖 +1、点赞 +1、分享 +1**。
`zeeho_like.py` 只做其中的「**每日点赞**」这一项 —— 不发帖、不评论、不分享，不向社区产出任何内容。

### 与签到的差异（改代码前必看）

| 项 | 签到 `zeeho_signin.py` | 点赞 `zeeho_like.py` |
| --- | --- | --- |
| **域名** | `h5.zeehoev.com`（H5 端） | **`tapi.zeehoev.com`（App 端）** |
| appId | `AiTXmBrm` | **`S7qPWPU1`** |
| appSecret | `70c2c7458ab88ca9504ad05…` | **`c5e0da7f4da28df805694ec3…`** |
| 额外头 | — | `user_id`、`interfaceversion: 2` |
| 需要 userId | 否 | **是** |
| 定时 | 每天 08:30 | 每天 08:50 |

两套体系**互不通用**：拿 H5 的 appId 去打 App 端接口会被拦成 `30121 permit error`。

### 接口清单

| 方法 | 路径（`https://tapi.zeehoev.com`） | 用途 |
| --- | --- | --- |
| GET | `/v1.0/mine/cfmotoservermine/setting/{userId}` | 查用户资料，含 `data.score` 积分余额 |
| GET | `/v1.0/social/cfmotoserversocial/community/mineArticleInfo?userId=&page=1&pageSize=10` | 取本人动态列表 |
| GET | `/v1.0/social/cfmotoserversocial/community/qbTzInfoNewV2?page=1&pageSize=20&postModule=2&slidingType=1` | 公共推荐流（兜底用，默认关闭） |
| **POST** | `/v1.0/social/cfmotoserversocial/socialCommu/likeFavoriteInfo` | **点赞**，body `{"postId":"…","kindFlag":"0"}` |

### 最关键的坑：body 必须参与签名

App 端签名串结构与 H5 完全一致：

```
raw  = 查询串 + 请求体 + "appId=S7qPWPU1&nonce=<uuid>&timestamp=<ms>" + <APP_SECRET>
sign = md5( sha1(raw) 的 hex )
```

但请求体必须是**实际发出的紧凑 JSON**（`json.dumps(body, separators=(',',':'))`）。
用空串签名时，GET 能过，**POST 一律返回 `30121 permit error`** —— 这是逆向时最卡人的一步。

### userId 是**可选的**，脚本会自己想办法

脚本按三级降级来解决 userId，绝大多数情况下你什么都不用配：

1. **自动解析**：用同一个 token 去打 H5 端的用户信息接口
   （`/cfmotoservermine/H5/baseInfo`），返回体里通常就带数字 id，脚本自动取用
2. **按 token 识别**：即使拿不到 id，查本人动态时不传 `userId` 参数，服务端会根据
   `Authorization` 判断是哪个用户
3. **明确提示**：以上都不行时，日志会给出可操作建议，而不会再笼统报「缺少 userId」

只有当你想看**积分余额**（`data.score`）时才真正需要 userId —— 因为它嵌在 URL 路径里。

确实想知道的话，三种方式：

1. **脚本自助获取**
   ```bash
   python zeeho_like.py --token 你的token --resolve-uid
   ```
   优先走 H5 端，命中就直接打印 `ZEEHO_TOKENS = <token>:<userId>`。

2. **手机抓包**
   MitM 监听 `tapi.zeehoev.com`，打开极核 App 的「我的」页，
   请求 URL `/v1.0/mine/cfmotoservermine/setting/<数字>` 里那段**数字**就是 userId。

3. **面板方案**：装 [cluck798/zeeho](https://github.com/cluck798/zeeho) 的 BoxJS 面板，点「获取ID」。

### 配置

| Secret | 值 |
| --- | --- |
| `ZEEHO_TOKENS` | Bearer Token，多账号用 `@` 分隔。**直接填签到用的那串即可** |
| `ZEEHO_POST_ID` | **推荐**。要点赞的动态 ID（或整条分享链接），见下方★ |
| `ZEEHO_USER_IDS` | 可选。逗号分隔的 userId，只用于显示积分余额 |
| `ENABLE_COMMUNITY_LIKE` | 设为 `1`：自己没有动态时，允许点赞公共流里的热门帖（默认关闭） |

★ **一句话拿到 `ZEEHO_POST_ID`**（推荐，最省事）

在极核 App 里打开**你自己的某条动态** → 分享 → 复制链接，会得到形如：

```
https://h5.zeehoev.com/post?time=1792056147099&uuid=MjAyNjEwMTUwMDg4MDkzMTU=
```

那个 `uuid=` 就是 postId 的 Base64。直接把**整条链接**粘进 `ZEEHO_POST_ID` 即可，脚本自己解：

```bash
python zeeho_like.py --decode-url "https://h5.zeehoev.com/post?uuid=MjAyNjEwMTUwMDg4MDkzMTU="
# 解出的 postId = 20261015008809315
```

配了它，脚本会跳过动态查询直接点赞，**完全不依赖 userId**。
多账号时用 `@` 分隔；只填一个则所有账号共用。

> **透明度说明**：`ZEEHO_TOKENS` 现在是「token + userId」的组合格式，与签到任务共用同一个 Secret 也没问题 ——
> 签到脚本只取 `:` 前面的部分识别不到时会降级按纯 token 处理；如果你担心串号，
> 可以在点赞工作流里单独配一个 Secret（比如 `ZEEHO_LIKE_TOKENS`）并改 yml 里的环境变量名。

### 行为策略

默认**只点赞自己的动态**：

1. 先查本人动态列表，找到属于自己 userId 的帖子
2. 找不到就跳过并提示 —— **绝不默认去点赞别人**
3. 只有显式开启 `ENABLE_COMMUNITY_LIKE` 时，才会退而去赞公共流里 `likeTimes` 最高的热门帖

这样即使账号从未在社区发过言，也不会对陌生车主造成无意义的打扰。

### 风险提示

- 行为类积分比签到**更容易触发风控**，请只用于自己的主账号，不要用多开小号刷
- 脚本每步之间加了随机 3–8 秒延时，多账号还会随机错峰至多 10 分钟，尽量模拟真人节奏
- 一旦出现 `30121/30124/30125 permit error`，说明服务端又轮换密钥了，需要重新抓接口

## 六、常见问题

**Q：Actions 里报 `未配置 secrets.ZEEHO_TOKENS`**
A：Secret 名字要完全一致，且大小写敏感。

**Q：提示连不上 / 超时**
A：`h5.zeehoev.com` 是国内域名。GitHub Actions 本身是直连的，一般不会遇到；本地跑如果挂了代理，关掉或加直连规则。

**Q：定时任务没跑**
A：GitHub 对 scheduled workflow 有两条限制——高峰可能延迟几分钟到十几分钟；仓库 **60 天无任何活动**会被自动停用定时任务。偶尔手动 Run 一下就能恢复。

**Q：想改签到时间**
A：改 `.github/workflows/signin.yml` 里的 cron。**注意是 UTC**：

| 北京时间 | cron（UTC） |
| --- | --- |
| 08:30 | `30 0 * * *` |
| 09:00 | `0 1 * * *` |
| 00:30 | `30 16 * * *` |

**Q：支持多账号吗**
A：支持。`ZEEHO_TOKENS` 里用逗号或换行分隔多个 Token，脚本会依次执行并汇总。

---

## 七、目录结构

```
.
├── .github/
│   ├── workflows/signin.yml   # 定时任务：每日 08:30 签到
│   └── workflows/like.yml     # 定时任务：每日 08:50 点赞
├── zeeho_signin.py            # 签到脚本（H5 端，纯标准库）
├── zeeho_like.py              # 点赞脚本（App 端，纯标准库）
├── probe_social.py            # 社区接口探测工具（需真 token）
├── .gitignore                 # 已排除凭据文件
└── README.md
```

---

## 八、致谢

- 签到流程与签名算法来自 [MonicaGmm/zeeho-quanx](https://github.com/MonicaGmm/zeeho-quanx)
- macOS 菜单栏版本见 [desfate/macOS_for_zeeho_auto_signin](https://github.com/desfate/macOS_for_zeeho_auto_signin)
- App 端社区接口（`tapi.zeehoev.com`、`S7qPWPU1`、likeFavoriteInfo 等）参考
  [cluck798/zeeho](https://github.com/cluck798/zeeho)；本仓库只取其中的**点赞**部分，
  并修正了它签名时漏传 body 导致 POST 被拦的问题

## 九、许可

MIT。使用本软件产生的账号封禁、积分异常、数据丢失等后果由使用者自行承担。
