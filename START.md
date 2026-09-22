# 下载之后展开

在Codex中打开这个仓库，告诉它：

> 请把这个基地车展开到我的项目目录，创建长期多对话协作岗位，所有岗位共用目标项目目录；项目目标是……。

Codex应先读目标项目的README与AGENTS，再执行以下流程：

1. `python basecamp.py init --target "目标项目目录"`。默认一个Main业务域，每域13岗。用`--domain 业务域`可设置领域（允许重复参数），`--layout compact`为5岗编制。先加`--dry-run`可预览文件变化。
2. 初始化会安装自包含运行器、岗位技能、协作规则和项目入口；保留已有README，给AGENTS追加有界入口，冲突文件不覆盖。`.codex-basecamp/`是新项目自己的本机状态，默认忽略。
3. 让用户在Codex桌面打开目标项目，继续目标项目 `.agents/basecamp/START.md`。运行`doctor --live`检查，按用户已要求的共享目录方式创建新岗位并记录真实地址。
4. 启动完成后由新项目主控承接目标。源仓库可以移走，已展开项目仍有全部框架代码、模板和技能。

也可克隆本仓库后在此目录直接展开：`python basecamp.py init --target . --name 我的项目`，再按安装后的START创建岗位。本仓库中的framework/templates/tests用于维护框架，本身不是业务目录。

要求Python>=3.11；实际自动岗位运行还需要兼容的Codex桌面、CLI及登录状态。文件可以离线安装；认证不随仓库传递。可用接口由doctor实测，不保证其他平台或所有未来版本均相同。
