# 会务服务：分级公开、场次安排与索引回执

本服务在《[论文目录交换](exchange.md)》的数据模型之上，把三条流程接成一个可用系统：

1. **投稿交接** —— 通讯作者逐版交稿，权利人按特定版本与公开范围签认，专利办按结果独立设定最早公开时间；
2. **公开审查** —— 编辑随时查询稿件/场次此刻能发什么、被谁挡住，专题组合可顺延或移出条目；
3. **索引站回执** —— 批组暂存、开放确认、拒绝、确认撤回四态对账，无回话歧义可追溯，已公开只发勘误或撤回。

所有时刻都带时区偏移；所有判定都以调用时显式给出的业务时刻 `at` 为准（缺省取服务当前时刻）。

## 判定规则

### 单稿可公开性（`level`）

* `full`：摘要授权齐备，摘要引用与全部补充件的权利人均在**该版本**签认且专利时钟已解除；
* `abstract`：只有简版可发。补充件有受阻内容，但摘要本身及其引用结果全部可公开；
* `blocked`：摘要直接引用了不能公开的结果，或摘要授权不齐 —— 任何形式（含无图版）都不能发。

要点：

* 签认按版本独立。一篇论文摘要变更产生新版本，不撤回另一篇论文或另一版本已批准的记录；两篇稿共享同一补充件时，各自需要在各自版本上取得签认。
* 专利时钟可多次下发。同一内容以评估时刻**已收到**的最新一条（`received` 最大）为准，因此申请变更后的新日期会覆盖旧日期，尚未收到的变更不影响当下决定。
* `abstract_ready_at` / `full_ready_at` 给出假设授权齐备时的最早可发时刻，用于回答“什么时候能如期上架”。

### 场次

* 专题合集（`grouped=true`）以整组为单位开放：每个论文槽位在候选版本中优先选全版、其次选可发简版；任一槽位无可行版本时状态为 `blocked`，存在未来恢复时刻时为 `delayed`，并保留各稿已签认的安排记录。
* 普通场次（`grouped=false`）可分批开放，槽位之间不互相阻塞。
* `deciders` 把决定追溯到具体版权签认（缺谁的签名）、专利时钟（哪条 embargo、何时解除）或索引站状态（含「已发出无回话，可能已公开」）。
* 条目可在取得该论文全体权利人同意后移出未发出的专题；已公开的条目只能走勘误/撤回。

### 索引批组

本地状态：`prepared → dispatched → staged → visible`，旁支 `stop_requested → stopped`、`rejected`、`withdrawn`。

* 暂存、开放确认、拒绝开放、确认撤回四种回执阶段不可互换；已公开（visible/withdrawn）不可回退，迟到的 staged 不能改写状态。
* 派发后没有回话（连接断开/超时）记为 `dispatched` 而非失败：语义是「可能已经公开」。重发沿用同一批号，服务拒绝把相同版本放进第二个有效批组，避免重复分配索引。
* 回执中的 `versions` 以索引站持有为准并原样留存；与本地批组不一致时返回 `version_conflict`，编辑新准备的稿不会覆盖索引站刚持有的旧版。
* 未生效批组可停止；已公开批组停止请求被拒，只接受 `erratum` / `withdrawal` 声明，公开的业务时刻 `happened` 永久可查。

## HTTP 接口

会务服务默认监听 `127.0.0.1:8088`。GET 的 `at` 与 POST 体中的 `at` 均为可选业务时刻（需 URL 编码）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 服务状态 |
| POST | `/v1/manuscripts` | 逐版交稿，标注摘要引用 `abstract_refs` 与补充件 `extras` |
| POST | `/v1/consents` | 权利人对特定版本的范围签认 |
| POST | `/v1/embargoes` | 登记/变更某结果的最早公开时间 |
| GET | `/v1/manuscripts/{id}/review?at=` | 单稿公开审查与逐条归因 |
| GET | `/v1/sessions` / `/v1/sessions/{id}/review?at=` | 场次列表 / 当刻能否按计划开放 |
| POST | `/v1/sessions/choose` | 专题主持人选定权利完整的组合 |
| POST | `/v1/sessions/reschedule` | 按场次约定调整条目公开时间 |
| POST | `/v1/sessions/remove-entry` | 经全体权利人同意移出条目 |
| POST | `/v1/batches` | 建立索引批组（受阻旧版与版本重叠会被拒） |
| POST | `/v1/batches/{id}/dispatch` | 发往索引站；无回话标 `dispatched` |
| POST | `/v1/batches/{id}/stop` | 停止未生效批组 |
| POST | `/v1/reconcile` | 拉取回执对账（503 原样上抛） |
| GET | `/v1/batches` / `/v1/batches/{id}` | 批组状态、回执、派发流水、勘误记录 |
| POST | `/v1/batches/{id}/notices` | 已公开批组发 erratum/withdrawal |
| GET | `/v1/authors/{actor}?at=` | 作者视图，按授权范围遮蔽未公开内容与保密原因 |

错误统一为 `{"message": ..., "details": ...}`，状态码取 404（不存在）、409（冲突）、422（校验失败）、503（索引站不可用）。

## 运行

```bash
# 1. 索引站回执回放（可选，用于联调）
python3 partner.py --port 8087 --journal run/ingress.db

# 2. 会务服务（首次启动自动载入 fixtures/exchange.json 的基础资料）
python3 -m conference.main --db run/conference.db --index http://127.0.0.1:8087

# 测试
python3 -m unittest discover -s tests -v
```

存储为单个 SQLite 文件；内存模式（`:memory:`）供测试使用。回执只通过 `reconcile` 入库，启动加载交换资料时不导入 `receipts`，符合「读到回执不代表本地做过操作」的约定。
