# ZEEHO 极核每日自动化（签到 + 互动）

跑在 **GitHub Actions** 上，无需服务器、无需手机常开。**一次运行做完两件事，先签到，后互动：**

```
① 签到   +1 分（连签另有盲盒）
② 互动   发帖 +1 / 点赞 +1 / 分享 +1，最多 3 分
```

签到流程移植自 [MonicaGmm/zeeho-quanx](https://github.com/MonicaGmm/zeeho-quanx)（Quantumult X 版），
互动流程移植自 [cluck798/zeeho](https://github.com/cluck798/zeeho) 的 Android 版
（源码来自 `ZEEHO助手.apk` 逆向），本仓库是两者的 Python 合并实现。

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
| `ZEEHO_TOKENS` | `<token>:<userId>`，多账号用 `@` 分隔。**纯 token 也能跑** |

可选：

| Name | Value |
| --- | --- |
| `ZEEHO_POST_ID` | 指定已有动态 ID（或分享链接）。填了就只点赞，不发帖不删帖 |
| `ZEEHO_POST_CONTENT` | 临时帖内容，默认 `开心的一天` |
| `INTERACT_STEPS` | 逗号分隔 `publish,like,share,delete`，不填默认全做 |
| `NOTIFY_BARK` | Bark 地址，如 `https://api.day.app/你的key` |
| `NOTIFY_SERVERCHAN` | Server酱 SendKey |

### 3. 启用并手动测试

**Actions → ZEEHO 每日自动化 → Run workflow**，先跑一次确认能通。

之后每天 **08:30（北京时间）** 自动执行，签到完成后紧接着跑互动。

> ⚠️ **私有仓库 + 免费账号不会跑定时任务**，见文末「常见问题」最后一条。

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
export ZEEHO_TOKENS="uuid1:1234567@uuid2:7654321"

python zeeho.py                       # 先签到，后互动（默认）
python zeeho.py --signin-only         # 只签到
python zeeho.py --interact-only       # 只互动
python zeeho.py --status-only         # 只查状态，不执行
python zeeho.py --dry-run             # 只自检，不联网
python zeeho.py --token "uuid"        # 单个 Token 直接传
python zeeho.py --steps publish,like  # 自定义互动步骤
python zeeho.py --content "今天天气不错"
python zeeho.py --keep                # 发完帖不删除
python zeeho.py --resolve-uid         # 自助获取 userId
python zeeho.py --decode-url "https://h5.zeehoev.com/post?uuid=xxx"
```

---

## 四、接口说明（踩坑记录， valuable ）

这些是逆向 + 实测得到的，改脚本前先看这里。

两件事走**完全不同的两套后端**：

| 项 | 签到 | 互动 |
| --- | --- | --- |
| Host | `https://h5.zeehoev.com` | `https://tapi.zeehoev.com` |
| appId | `AiTXmBrm` | `S7qPWPU1` |
| appSecret | `70c2c7458ab88ca9504ad0521f170075bc91f2f7` | `c5e0da7f4da28df805694ec3dd1fc6792e9df99d` |
| 鉴权 | `Authorization: Bearer <token>`（UUID） | 同 |
| 额外头 | `Origin` / `Referer` / `Zeeho-User-Agent` | `user_id` / `interfaceversion: 2` |

### 签名算法

两端公式一致：

```
param = "appId=<APP_ID>&nonce=<nonce>&timestamp=<毫秒>"
v     = 查询串 + 请求体 + param + <APP_SECRET>
sign  = md5( sha1(v) )
```

容易出错的三处：

1. **查询串要按 key 字典序排序**（App 端 `SignUtil.toQuery` 里是 `Collections.sort`）。
   插入顺序和排序顺序在多参数时不同，签名就会对不上。签到端只有一个参数所以没暴露。
2. **App 端 nonce 不是 UUID**，是 `<毫秒时间戳> + 16 位随机字母数字`；
   只有 H5 端才用 UUID。
3. **请求体必须是实际发出的紧凑 JSON**（`json.dumps(body, separators=(',',':'))`）。
   用空串签名时 GET 能过，**POST 一律被拦 `30121 permit error`**。
   另外 DELETE 时 body 强制置空。

其余规则：查询串不做 URL 编码；GET 请求体为空串；`md5(sha1(...))` 是两层，
sha1 输出 hex 字符串后再 md5。

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
- **如果所有账号都报 30125 / 30121 / permit error，就是又轮换了**，重新抓包换 `zeeho.py` 顶部的两个常量。签名算法本身（`md5(sha1(...))`）一直没变。

---

## 五、互动任务（发帖 + 点赞 + 分享）

极核的互动任务是分开计分的：**发帖 +1、点赞 +1、分享 +1**，一天最多 3 分。
`zeeho.py` 把这三件都做了，和签到在同一个脚本里按顺序执行。

### 发的是「临时帖」，最后会删掉

流程照搬 `ZEEHO助手.apk` 的 `TaskRunner.autoPublish`：

```
发帖 → 等 2~4s → 点赞 → 等 2~4s → 分享 → 领分享积分 → 等 2~4s → 删除该帖
```

也就是说每天只是**临时创建一条动态**，拿完 3 分就删掉，**不会在你主页留下任何内容**。
发帖内容默认「开心的一天」，可用 `ZEEHO_POST_CONTENT` 改。

**删除有硬保护**：只有本次由脚本自己创建的动态才会被删。
如果你通过 `ZEEHO_POST_ID` 指定了已有动态，删除步骤会直接跳过并记一行日志 ——
绝不会删你真实发过的内容。

### 互动接口清单

| 方法 | 路径（`https://tapi.zeehoev.com`） | 用途 |
| --- | --- | --- |
| GET | `/v1.0/mine/cfmotoservermine/integral/totalIntegral` | 积分余额，**不需要 userId** |
| GET | `/v1.0/mine/cfmotoservermine/setting/{userId}` | 用户资料，含 `data.score` |
| GET | `…/community/mineArticleInfo?userId=&page=1&pageSize=10` | 取本人动态列表 |
| GET | `…/community/qbTzInfoNewV2?page=1&pageSize=20&postModule=2&slidingType=1` | 公共推荐流（默认关闭） |
| POST | `…/commonArticle` | 发帖：`{postSubInfo:{topicList:[]}, topicid:"", postcontent:"文本"}` |
| POST | `…/socialCommu/likeFavoriteInfo` | **点赞**，body `{"postId":"…","kindFlag":"0"}` |
| PUT | `…/article/share/{postId}` | 分享（无 body） |
| DELETE | `…/commonArticle/deleteArticle?articleId=&postType=1` | 删帖 |
| GET | `/v1.0/mine/…/integral/adjustByShare` | 领取分享积分 |

> 缺 `slidingType` 会报 `40004 滑动类型不能为空`。

执行顺序（移植自 APK 的 `TaskRunner.autoPublish`）：
发帖 → 等 2~4s → 点赞 → 等 2~4s → 分享 → 领积分 → 等 2~4s → 删除。
取动态时 ID 依次试 `tuuid` → `id` → `articleId`。

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
   python zeeho.py --token 你的token --resolve-uid
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
| `INTERACT_STEPS` | 可选。逗号分隔 `publish,like,share,delete`。**不填默认全做** |
| `ZEEHO_POST_ID` | 可选。指定已有动态 ID（或分享链接）。**填了就只做点赞** |
| `ZEEHO_POST_CONTENT` | 可选。临时帖内容，默认 `开心的一天` |
| `ZEEHO_USER_IDS` | 可选。只用于多一层积分查询，非必需 |
| `ENABLE_COMMUNITY_LIKE` | 设为 `1`：拿不到动态时，允许点赞推荐流热门帖（默认关闭） |

步骤自动推断规则：

- 什么都没配 → `publish,like,share,delete`（完整 3 分流程）
- 配了 `ZEEHO_POST_ID` → 自动降为 `like` 单一项，不发帖、不删除
- 配了 `INTERACT_STEPS` → 以它为准（例：`publish,like` 表示发帖+点赞但**保留该帖不删**）

**「只点赞」模式**：想退回早期行为（不发帖、不分享，只 +1 分），
把 `INTERACT_STEPS` 设成 `like` 即可。

`ZEEHO_POST_ID` 可以从动态分享链接拿到（`uuid` 参数是 postId 的 Base64）：

```
https://h5.zeehoev.com/post?time=1792056147099&uuid=MjAyNjEwMTUwMDg4MDkzMTU=
```

整条链接粘进 `ZEEHO_POST_ID` 即可，脚本自己解。也可以本地跑
`python zeeho.py --decode-url "<链接>"` 查看解出的 postId：

```bash
python zeeho.py --decode-url "https://h5.zeehoev.com/post?uuid=MjAyNjEwMTUwMDg4MDkzMTU="
# 解出的 postId = 20261015008809315
```

配了它，脚本会跳过动态查询直接点赞，**完全不依赖 userId**。
多账号时用 `@` 分隔；只填一个则所有账号共用。

### 备用签到路径（服务端换 key 时的退路）

`ZEEHO助手.apk` 走的是**另一套**签到接口，已实测同样可用（假 token 均返回 401，即签名通过）：

| | 现有脚本 | 备用 |
| --- | --- | --- |
| 路径 | `/cfmotoservermine/**H5/**signin` | `/cfmotoservermine/signin`（**无 H5 前缀**） |
| 密钥 | `AiTXmBrm` + `70c2…` | `S7qPWPU1` + `c5e0…`（复用 App 端那套） |
| 额外参数 | — | `server_name=SMART` |
| nonce | UUID | UUID（走 h5 分支） |

若哪天签到报 `30125 permit error`（密钥轮换），把 `zeeho.py` 里的
`H5_APP_ID`/`H5_APP_SECRET` 换成 `S7qPWPU1`/`c5e0…`、路径去掉 `/H5/`、GET 参数加上
`server_name=SMART`，多半就能救回来。

### 行为策略

1. **默认只操作自己的内容**。公共流（别人的帖子）只在显式开启
   `ENABLE_COMMUNITY_LIKE=1` 时才会碰，且只会挑 `likeTimes` 最高的热门帖。
2. **不留痕**。临时帖创建后必然尝试删除；删除失败也只记日志，不影响已拿到的分。
3. **节奏模拟真人**。每个步骤之间随机 2–4 秒，多账号之间再随机错峰至多 10 分钟。
4. **任何一步失败都不阻断后续**。发帖失败会继续尝试查已有动态并点赞。

### 风险提示

- 行为类积分比签到**更容易触发风控**，请只用于自己的主账号，不要用多开小号刷
- 临时帖会在社区短暂存在（约 10 秒），期间理论上可能被他人刷到
- 一旦出现 `30121/30124/30125 permit error`，说明服务端又轮换密钥了，需要重新抓接口
- 不想发帖的话，把 `INTERACT_STEPS` 设成 `like` 即可退回「只点赞」模式

## 六、常见问题

**Q：Actions 里报 `未配置 secrets.ZEEHO_TOKENS`**
A：Secret 名字要完全一致，且大小写敏感。

**Q：提示连不上 / 超时**
A：`h5.zeehoev.com` 是国内域名。GitHub Actions 本身是直连的，一般不会遇到；本地跑如果挂了代理，关掉或加直连规则。

**Q：定时任务完全没跑，但手动 Run 一切正常**
A：**这是最常见的原因，而且 GitHub 文档里几乎没写** ——

> **免费个人账号 + 私有仓库 = 不支持 `schedule` 定时触发。**

`workflow_dispatch`（手动）完全正常，cron 却永远不触发。API 侧表现很有迷惑性：
workflow 的 `state` 依然是 `active`，`actions/permissions` 也是 `enabled: true`，
运行记录里只会看到手动触发的那些，`event=schedule` 的记录数是 0。

确认方法（需要 GitHub API）：

```bash
curl -H "Authorization: token <PAT>" \
  "https://api.github.com/repos/<owner>/<repo>/actions/runs?per_page=15"
# 看 event 字段：全是 workflow_dispatch 而 schedule 是 0，就是这个限制
```

三种解法：

| 方案 | 成本 | 说明 |
| --- | --- | --- |
| 仓库改公开 | 0 | 立即生效。前提是确认仓库里没有 Token（本仓库已扫过全历史） |
| 升级 GitHub Pro | \$4/月 | 私有仓库即可用定时 |
| 外部 cron + `workflow_dispatch` API | 0 | cron-job.org 等定时调 `POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches`，需带 `workflow` 权限的 PAT |

**其他可能**（本仓库已全部排除）：默认分支不对、cron 语法错、仓库是 fork、
60 天无活动被自动停用、Actions 被设置禁用、分钟数配额用尽。

> 小技巧：想确认定时是否恢复，可以临时加一个只 `echo` 的探针 workflow，
> cron 设成 `*/5 * * * *`，几分钟内就能从运行记录里看到 `event=schedule`，
> 确认后删掉它，别用它跑正式任务。

**Q：定时任务没跑（其他原因）**
A：GitHub 对 scheduled workflow 还有两条限制——高峰可能延迟几分钟到十几分钟；仓库 **60 天无任何活动**会被自动停用定时任务。偶尔手动 Run 一下就能恢复。

**Q：想改签到时间**
A：改 `.github/workflows/daily.yml` 里的 cron。**注意是 UTC**：

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
├── .github/workflows/daily.yml   # 唯一的定时任务：每日 08:30，先签到后互动
├── zeeho.py                      # 合并脚本（纯标准库，签到 + 互动）
├── .gitignore                    # 已排除凭据文件
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
