# ZEEHO 极核每日自动签到

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

## 五、常见问题

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

## 六、目录结构

```
.
├── .github/workflows/signin.yml   # GitHub Actions 定时任务
├── zeeho_signin.py                # 签到脚本（纯标准库）
├── .gitignore                     # 已排除凭据文件
└── README.md
```

---

## 七、致谢

- 签到流程与签名算法来自 [MonicaGmm/zeeho-quanx](https://github.com/MonicaGmm/zeeho-quanx)
- macOS 菜单栏版本见 [desfate/macOS_for_zeeho_auto_signin](https://github.com/desfate/macOS_for_zeeho_auto_signin)

## 八、许可

MIT。使用本软件产生的账号封禁、积分异常、数据丢失等后果由使用者自行承担。
