# 论文目录交换

版本号作用域为 paper。图表、摘要引用结果都用 content id 表示，无引用内容使用空数组。签名授权范围包括批准的内容集合，缺少某个权利人签名时该内容不可公开。earliest 是专利办许可的最早公开时刻，按结果分别生效。正式索引号由对接方发回；暂存、开放确认、拒绝开放、确认已撤回的状态不可互换。

资料使用 UTF-8 JSON。每个集合各自维护记录 id，跨集合引用使用相应记录 id。事件中的 happened 是业务发生时间，arrived 是对接方可提供该回执的时间。到达时间不能代替业务时间。这些记录来自不同流程阶段，读到一个回执并不代表本地已做过相应操作。

## `works`

论文作者、编辑及研究组。字段为 `id`, `owners`, `editor`。

## `pieces`

结果和图表以及各自权利人。字段为 `id`, `kind`, `owners`。

## `manuscripts`

版面及摘要直接提到的内容。字段为 `id`, `paper`, `version`, `abstract_refs`, `extras`。

## `consents`

作者签认或单项权利人的公开许可。字段为 `id`, `version`, `actor`, `scope`, `signed`。

## `embargo`

由专利办发送的独立内容公开时钟。字段为 `id`, `content`, `earliest`, `received`。

## `sessions`

专题/普通场次及选稿候选，按论文独立列可发布版本。字段为 `id`, `grouped`, `publish`, `candidates`。

## `receipts`

索引站只能凭外部批号和稿组对账。grant_ids 保留实际索引号。字段为 `id`, `batch`, `phase`, `versions`, `grant_ids`, `happened`, `arrived`。

## 回执查询

本机回放只提供对接方的既有回执。`GET /v1/receipts?at=...` 的 `at` 是 URL 编码后的带偏移时间，返回对象中 `receipts` 是在该时间之前及当时可取得的回执。同一条数据可以在连续查询中出现，列表先后不代表业务先后。`503` 表示当前无法查询回执。

指定 `--journal` 时，`POST /v1/dispatches` 接收本地联调请求并登记原文。接收只代表到达对手方，不会生成履约回执。指定 `--drop-reply` 可在接收后断开。请求正文由业务客户端提供，回执仍由交换资料独立给出。
