# 多渠道招聘岗位归并与薪资口径归一（`jobmerge`）

> 数据工程 · 中型多模块项目。原生 HTML/CSS/JS 前端 + Python FastAPI 后端 + Celery worker + PostgreSQL 16 + Redis，5 个容器一键起。
>
> 视觉方向：**报纸分类广告**——墨黑 + 套红 + 米灰纸底，思源宋正文 + 等宽检字；登录页是「取报单」，总览是头版。

## 1. 场景

- **模拟客户**：云雀招聘聚合平台 · 岗位运营组（8 人），为 60 家企业客户做岗位聚合检索。
- **现有工作流**：运营每天从 12 个招聘渠道（各自独立采集器）把岗位拉进 Excel，人工挑重复、手算月薪，再导给检索侧。
- **三个痛点**：
  1. 同一岗位在 4–6 个渠道重复挂载，检索结果里同一职位刷屏；
  2. 薪资写法混乱（`8-12K·13薪`、`年薪18万`、`面议`、`4000-6000元/月`），排序不可比；
  3. 过期岗位未下线，仍留在发布清单里。
- **上下游**：各渠道采集器 → **本系统（原始岗位入库 → 归一 → 归并 → 复核 → 发布清单）** → 检索服务 / 客户报表。
- **一天的工作剧本**（演示数据锚定 2026-11-14）：
  1. 06:00–08:00 四个采集器按 `crawl_cron` 抓取，岗位落到 `posts`；
  2. 08:20 运营专员打开头版，看到 160 条来稿、薪资不可比 18 条（11.25%）；
  3. 08:30 在「归并结果」按 0.82 阈值发起归并：160 条 → 118 簇，消重 42 条；
  4. 08:40 系统标记 12 个簇需要人工确认（13 薪写法差异 / 公司名全称与简称）；
  5. 09:00 专员逐个打开簇详情，看成员对照与相似度，合并或拆分；
  6. 09:30 管理员导出 `jobmerge-clusters.csv`（118 行）给检索侧；
  7. 10:00 客户投诉某个岗位名字脱敏后无法核对，管理员去「审计日志」定位导出与复核记录；
  8. 次日 07:40 worker 定时重算一次，过期岗位自动下线。

## 2. 角色与权限

| 角色 | 账号 | 权限 | 典型动作 |
|---|---|---|---|
| 平台管理员 `admin` | `admin@jobmerge.io` | 渠道读写 + 归并 + 复核 + 导出 + 审计 | 维护渠道采集计划与阈值、发起归并、导出清单、查审计 |
| 岗位运营专员 `analyst` | `analyst@jobmerge.io` | 渠道只读 + 归并 + 复核 + 导出 | 发起归并、逐个复核归并簇、导出复核记录 |
| 客户只读 `viewer` | `viewer@jobmerge.io` | 只读（公司名脱敏） | 看头版与归并结果；**不能**发起归并、复核、导出、看审计 |

密码统一：`Demo@2026`。低权角色调用高权接口返回 **403**（真实 HTTP 状态，不是前端隐藏）：

- `POST /api/channels`、`PATCH /api/channels/{id}`：仅 admin（viewer / analyst 均 403）
- `POST /api/runs`、`POST /api/reviews`、`POST /api/ingest/batches`：admin + analyst（viewer 403）
- `GET /api/exports/*.csv`：admin + analyst（viewer 403）
- `GET /api/ingest/ledger`、`GET /api/ingest/batches*`：全部角色可读（viewer 看到的是脱敏后的岗位数据）
- `GET /api/audit`：仅 admin（viewer / analyst 403）
- 无 token → 401；非法参数 → 422；重复自然键（渠道编码）→ 409；回执缺应有数 → 422

## 3. 业务模块表（7 个）

| # | 模块 | 对应页面 | 后端 services / controller | 主要 API |
|---|---|---|---|---|
| ① | 渠道与采集配置 | 渠道与采集 | `channel_service` / `channel_controller` | `GET/POST /api/channels`、`PATCH /api/channels/{id}` |
| ② | 归一解析（薪资/公司/职位词干） | 原始岗位（+ 口径试算） | `salary_norm`（算法 A）、`normalize_service` / `post_controller`、`salary_controller` | `GET /api/posts`、`GET /api/posts/{id}`、`POST /api/salary/normalize`、`GET /api/salary/samples` |
| ③ | 指纹与分块 | 归并结果（分块面板） | `dedupe`（算法 B 的 blocking）、`merge_service` / `run_controller` | `POST /api/runs`、`GET /api/runs`、`GET /api/runs/{id}` |
| ④ | 相似度归并（golden record） | 归并结果 | `dedupe.build_clusters`、`merge_service`、`cluster_service` / `cluster_controller` | `GET /api/clusters`、`GET /api/clusters/{id}` |
| ⑤ | 人工复核（合并 / 拆分 / 保持） | 复核与导出 | `review_service` / `review_controller` | `GET /api/reviews/pending`、`GET /api/reviews`、`POST /api/reviews` |
| ⑥ | 结果导出与审计 | 复核与导出（导出面板 + 审计） | `export_service`、`audit_service` / `export_controller`、`audit` 路由 | `GET /api/exports/{clusters,posts,reviews,runs,ingest-batches}.csv`、`GET /api/audit` |
| ⑦ | 来稿批次核对与发布口径 | 来稿批次（+ 头版横幅） | `ingest_service` / `ingest_controller` | `POST /api/ingest/batches`、`GET /api/ingest/batches`、`GET /api/ingest/batches/{id}`、`GET /api/ingest/ledger` |

> 登录（`auth_service`）、总览（`overview_service`）、限流与审计中间件属于横切能力，不计入业务模块。

### 模块⑦ 来稿批次口径（防止旧岗位冒充新稿）

各渠道采集器按批次回传，系统按批核对四个数并留痕（`ingest_batches` + `ingest_items`，原始回执不丢）：

- **应有数** `expected_count`：回执里 `expected_external_ids` 清单长度（或 `expected_count`）；
- **实到数** `arrived_count`：有效且去重后真实到达的岗位数（批内重复、缺字段无效行不计入）；
- **重复数** `duplicate_count`：批内重复回执 + 自然键（渠道+外部ID）已存在的来稿，**重试同一批不重复新增岗位**；
- **接收时间** `received_at`：首次接收时间，重试保留不覆盖，`retries` 记录重试次数。

批次核对结论 `status` 与岗位来稿状态 `ingest_state`、渠道发布口径 `release_state` 联动：

| 批次状态 | 含义 | 岗位处理 | 渠道发布口径 |
|---|---|---|---|
| `complete` 本批收齐 | 实到 = 应有 | 本批新稿 `fresh`，历史岗位转 `carried` | `releasable` 可发布 |
| `partial` 本批缺量 | 采集器报部分成功/超时或缺量 | 本批实到仍是 `fresh`；**旧岗位标 `carried` 沿用，不冒充新稿** | `carried` 旧稿沿用·过渡 |
| `failed` 本批失败 | 0 条有效新稿 | 该渠道岗位全部 `isolated` 隔离，不参与归并 | `isolated` 隔离不发布 |

只要有渠道未收齐，归并运行 `publish_complete=false`：**其他渠道照常处理，但只出过渡版，不发布成完整版本**；
含沿用/隔离成员的簇 `release_state=withheld`（暂缓发布），完整发布清单
（`GET /api/exports/clusters.csv?complete_only=true`）只含 `releasable` 簇，行数与运行的
`releasable_clusters` 计数逐项对上。

## 4. 页面（7 个）

| # | 路由 | 页面 | 说明 |
|---|---|---|---|
| 1 | `#/login` | 登录 · 取报单 | 报头 + 双栏导语 + 虚线取报单表单 |
| 2 | `#/overview` | 头版总览 | 头条综述、发布版本横幅、四个报眼数字、渠道批次口径、薪资口径分布、渠道来稿量、近 7 日归并、最新运行、成员最多簇 |
| 3 | `#/channels` | 渠道与采集 | 过滤条 + 检字表（采集计划/已入库/最近采集）+ 详情抽屉 + 登记渠道 |
| 4 | `#/ingest` | 来稿批次 | 渠道批次台账（应有/实到/重复/缺量/沿用/隔离/接收时间）+ 批次历史 + 逐条回执抽屉 + 模拟采集器回执（缺量/失败/重试） |
| 5 | `#/posts` | 原始岗位 | 薪资原值 vs 归一值对照 + 来稿状态（新稿/沿用/隔离）+ 岗位详情抽屉 + **算法 A 口径试算** |
| 6 | `#/clusters` | 归并结果 | 归并簇表（含发布口径列）+ 成员对照抽屉（相似度/golden/来稿状态）+ 发起归并 + 全量/完整发布版导出 |
| 7 | `#/reviews` | 复核与导出 | 待复核队列 + 复核历史 + 6 个 CSV 导出（含批次台账、完整发布版）+ 审计日志（仅 admin） |

共用组件：`frontend/site/js/ui.js`（`table` / `drawer` / `field` / `badge` / `barList` / `trendChart`）与
`frontend/site/js/components.js`（`filterBar` / `clusterTable` / `openClusterDrawer` / `salaryRange`），
被 4 个业务页共用（渠道/岗位/归并/复核）。

## 5. 快速启动

```bash
cp .env.example .env
docker compose up -d --build      # 等 5/5 healthy
open http://localhost:18200
```

首次启动 backend 自动建表并执行一次 seed（`SEED_DEMO=true`）。seed 幂等，可重复执行：

```bash
docker compose exec -T backend python -m scripts.seed_demo
# SEED_SUMMARY brand=jobmerge users=3 sources=4 runs=1 findings=118 pending=9 batches=4
```

seed 同时为 4 个渠道补来稿批次：3 个渠道本批收齐（complete），猎聘（liepin）报部分成功本批缺量
（partial，缺 2 条、2 条旧稿沿用），用于演示「缺量不冒充新稿、其他渠道先处理但只出过渡版」。

## 6. 访问地址

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost:18200 |
| 后端 API 文档 | http://localhost:19200/docs |
| 健康检查 | http://localhost:19200/api/health |
| 数据库映射 | localhost:17700 |

## 7. 技术栈

| 层 | 选型 |
|---|---|
| 前端 | 原生 HTML5 + CSS（oklch 令牌 / 报版栅格）+ ES Modules；无框架、无打包、无 CDN 脚本（仅 Google Fonts） |
| 后端 | Python 3.11、FastAPI 0.115、SQLAlchemy 2.0、PyJWT、passlib(bcrypt) |
| 异步 | Celery 5.4 + Redis（10 秒心跳、每日 07:40 归并重算） |
| 数据库 | PostgreSQL 16 |
| 部署 | Docker Compose，恰好 5 服务 |

## 8. 目录

```text
backend/
  src/config/{app,database,settings,celery_app}.py
  src/constants/{enums,roles,limits,error_codes,log_templates}.py
  src/models/{user,channel,post,cluster,run,review,audit,ingest_batch,ingest_item}.py
  src/schemas/{auth,channel,cluster,run,salary,ingest,common}.py
  src/services/
    salary_norm.py       # 算法 A：薪资口径归一（纯函数）
    normalize_service.py # 公司名 / 职位词干 / 城市 / 学历经验归一
    dedupe.py            # 算法 B：分块 + 相似度 + 并查集聚类
    merge_service.py     # 归并运行落库（含批次发布口径：隔离剔除 / 暂缓簇 / 完整发布版标记）
    ingest_service.py    # 来稿批次核对：应有/实到/重复/缺量、旧稿沿用与隔离、渠道发布台账
    channel_service.py post_service.py cluster_service.py review_service.py
    run_service.py overview_service.py export_service.py audit_service.py auth_service.py
  src/controllers/ src/routes/ src/middlewares/
  scripts/{seed_demo.py,demo_config.py,worker_healthcheck.sh}
  migrations/  tests/
frontend/site/{index.html,css/{tokens,app}.css,js/{api,app,config,store,ui,components}.js,js/pages/*.js}
```

## 9. 环境变量

见 `.env.example`：`COMPOSE_PROJECT_NAME`、`FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT`、`DB_*`、`JWT_SECRET`、`REDIS_URL`、`SEED_DEMO`、`RATE_LIMIT_*`、`APP_TIMEZONE_OFFSET_MINUTES`。

## 10. 核心算法与口径

### 算法 A｜薪资口径归一（`backend/src/services/salary_norm.py`）

输入一条薪资原值，输出 `(salary_low, salary_high, months)`、`monthly_low/high/median`、`confidence`、`norm_basis`。

1. 字符归一：NFKC 全角→半角，`～ ~ — – 至` → `-`，去空白；
2. `N薪` 提取发薪月数 `months`（缺省 12，合法 12–24）；
3. `面议 / 面谈 / 详谈 / 待定 / 保密 / 另议` → `status=negotiable`，`monthly_median=None`，`confidence=0.2`；
4. 金额单位：`k/千`=1000、`万/w`=10000；`8-12k` 这种只在后一个数字标单位时向左继承；
5. `年薪 / 年包`：先得年薪总额，再 ÷ 12 得月薪；
6. 月薪口径：`monthly_low = 名义下限 × 12 ÷ months`、`monthly_high = 名义上限 × 12 ÷ months`、
   `monthly_median = 区间中位 × 12 ÷ months`（四舍五入到元）；
7. 非法（空值 / 无金额 / 折月量级超出 500–500000）与矛盾（`low > high`）→ `status=unparsed`，
   进 `unparsed` 列表并计入 `fail_rate`；`noncomparable_rate = (unparsed + negotiable) / total`。

验收样例（与 `/api/salary/normalize` 返回一致）：

| 原值 | 名义月薪 | 归一后月薪 | 中位 | status |
|---|---|---|---|---|
| `8-12K·13薪` | 8000–12000 | 7385–11077 | **9231** | parsed |
| `年薪18万` | 15000–15000 | 15000–15000 | **15000** | parsed |
| `4000-6000元/月` | 4000–6000 | 4000–6000 | 5000 | parsed |
| `15-25K·14薪` | 15000–25000 | 12857–21429 | 17143 | parsed |
| `面议` | — | — | `null` | negotiable |
| `12-8K` | — | — | `null` | unparsed（区间矛盾） |

### 算法 B｜重复岗归并（`backend/src/services/dedupe.py`）

1. 归一：`company_norm`（去括号地域标注 + 去法人后缀 + 小写）、`title_stem`（去括号补充 + 去招聘噪声词 + 去标点）、
   `city`（去「市/省」）、`degree/experience` 枚举化；
2. blocking key = `company_norm + city + salary_bucket`，`salary_bucket = floor(monthly_median / 5000)`，同块内才两两比较；
3. 相似度：`0.45*title_jaccard + 0.25*company_sim + 0.20*salary_overlap + 0.10*attr_match`
   - `title_jaccard`：职位词干的字符集 Jaccard（中文不分词，见 §已知简化）
   - `company_sim`：归一后字符串相等 = 1.0，前缀包含 = 0.92，其余用 `SequenceMatcher` 比值
   - `salary_overlap`：两段月薪区间的交集/并集；双方都不可比 = 0.6，单方不可比 = 0
   - `attr_match`：学历与经验各占 0.5，相等 = 1.0，任一方「不限」= 0.7
4. `score >= threshold`（默认 0.82）连边，并查集聚类；
5. golden record = 「字段最全 → 发布最新 → id 最小」；簇置信度 = 簇内两两相似度均值；
6. 输出 `member_count`、`dup_removed`、`confidence`、`needs_review`（簇内发薪月数或公司写法不一致时为 true）。

seed 数据实算：160 条原始岗位 → **118 簇**（21 个多成员簇、最大 3 条、消重 **42** 条），
其中 21 个多成员簇里有 12 个被标记为待人工复核，8 个簇因过期下线。

## 11. 共享枚举出现位置

| 枚举 | 后端 | 前端 |
|---|---|---|
| 角色 `admin/analyst/viewer` | `backend/src/constants/enums.py`、`constants/roles.py` | `frontend/site/js/config.js`（`ROLE_LABELS` / `PERMISSIONS`） |
| 薪资口径状态 `parsed/negotiable/unparsed` | `constants/enums.py`（`ParseStatus`） | `config.js`（`ENUMS.parseStatus`、`parseTone`） |
| 归并簇状态 `active/expired/merged` | `constants/enums.py`（`ClusterStatus`） | `config.js`（`ENUMS.clusterStatus`、`statusTone`） |
| 复核结论 `merge/split/keep` | `constants/enums.py`（`ReviewDecision`） | `config.js`（`ENUMS.decision`、`decisionTone`） |
| 运行状态 `queued/running/success/partial/failed` | `constants/enums.py`（`RunStatus`） | `config.js`（`ENUMS.runStatus`） |
| 触发方式 `manual/schedule/seed` | `constants/enums.py`（`TriggerKind`） | `config.js`（`ENUMS.trigger`） |
| 渠道类型 `site/api/file/manual` | `constants/enums.py`（`ChannelKind`） | `config.js`（`ENUMS.channelKind`） |
| 学历 `any/college/bachelor/master/doctor` | `constants/enums.py`（`Degree`） | `config.js`（`ENUMS.degree`） |
| 经验 `any/1-3/3-5/5-10/10+` | `constants/enums.py`（`Experience`） | `config.js`（`ENUMS.experience`） |
| 来稿批次 `complete/partial/failed` | `constants/enums.py`（`BatchStatus`） | `config.js`（`ENUMS.batchStatus`、`batchTone`） |
| 岗位来稿状态 `fresh/carried/isolated` | `constants/enums.py`（`PostIngestState`） | `config.js`（`ENUMS.ingestState`、`ingestTone`） |
| 发布口径 `releasable/carried/isolated/withheld` | `constants/enums.py`（`ReleaseState`） | `config.js`（`ENUMS.releaseState`、`releaseTone`） |
| 回执结果 `inserted/duplicate/invalid` | `constants/enums.py`（`ItemOutcome`） | `config.js`（`ENUMS` 由批次抽屉映射） |

## 12. Docker 说明

- 5 个服务：`frontend`(Nginx 静态站) / `backend`(uvicorn) / `worker`(Celery) / `db`(PostgreSQL 16) / `redis`，全部带 healthcheck。
- 命名卷：`db_data`、`redis_data`；`service_healthy` 依赖；端口 `${FRONTEND_PORT:-18200}` 等写法。
- Nginx：`location /api/ { proxy_pass http://backend:8000/; }` + SPA 回落。
- 收尾：`docker compose down -v --remove-orphans`。

## 13. 已知简化

见 `test-artifacts/gb-611/TESTING.md`「已知差异」：字符集 Jaccard 代替分词、单个簇只保留一条有效复核结论、渠道采集以 seed 数据模拟（未接真实爬虫）。

## License

内部演示项目，仅用于评测与教学。
