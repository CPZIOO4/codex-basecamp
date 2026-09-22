# 框架实现

`cli.py` 统一命令；`scaffold.py` 安装与文件冲突检查；`provision.py` 岗位创建和持久登记；`settings.py` 路径与CLI发现；`mailroom.py` 原生队列和回执；`native_desktop.py` 当前桌面能力连接；`locking.py` 进程互斥。没有常驻研究调度器。

安装副本位于目标项目 `.agents/basecamp/`，本机状态独立保存在 `.codex-basecamp/`。不从来源项目读取配置。
