# 材料会议论文的分级公开与索引回执

研究组以稿件版号、权利人授权、专利公开时点和索引批号交换会务数据。

《论文目录交换》说明在 [交换约定](docs/exchange.md)，会务侧服务的规则与接口在 [服务说明](docs/service.md)。`fixtures/exchange.json` 是本机联调使用的合成记录。项目当前可读取这些交换文件，回放对接方回执，并验证资料之间的引用。

运行环境为 Python 3.11.2，只依赖标准库。执行 `python3 -m unittest discover -s tests -v` 运行全部测试（交换资料校验、领域判定、场次安排、批组对账与端到端 HTTP）。

* `python3 partner.py` 启动索引站回执回放，回环地址 8087 端口，`GET /health` 返回服务状态；`--unavailable` 模拟查询暂时失败，`--journal` 记录派发原文，`--drop-reply` 模拟接收后断连。
* `python3 -m conference.main` 启动会务服务，默认 8088 端口，SQLite 落盘于 `run/conference.db`，通过 `--index` 对接回执服务，`--unavailable` 时不连接索引站。
