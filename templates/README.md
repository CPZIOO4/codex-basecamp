# 本项目多对话协作

[START.md](START.md) 负责展开；[WORKFLOW.md](WORKFLOW.md) 规定岗位运行；`project.json` 保存领域与岗位编制；技能位于 `../skills/`。

统一入口：`python .agents/basecamp/run.py`，从项目根目录运行。

- `doctor --live`：只读检查CLI、桌面能力及原生队列接口；不创建岗位、不发任务。
- `provision`：预览尚未创建的岗位；实际创建按 START.md 的用户授权范围执行。
- `status --live`：查看已登记岗位的即时状态，空闲不做持续轮询。
- `projects`：列出桌面已登记项目，找到目标目录的真实projectId。
- `send --to "Main / 规划场1" --file 路径.md`：正文来自UTF-8文件，按接收方原生排队，不打断忙碌岗位。
- `wake --receipt 回执路径`：只重试已入队消息的桌面激活，不再次发送正文。

运行状态、真实岗位地址、回执及本机配置位于项目 `.codex-basecamp/`，默认不进入Git。消息accepted仅表示入队；activation另外报告是否启动。发送unknown只阻塞该接收方，主控先核查，不能用删状态、换ID或重复正文规避不明结果。

当前内置适配针对Windows Codex桌面与支持`thread/queue/add`的CLI。它调用本机已安装的工具，不复制桌面程序。Mac/Linux可以展开项目文件；实际自动创建、队列与激活须由doctor确认，不能把跨平台文件安装当作桌面支持证明。
