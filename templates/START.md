# 在本项目展开基地车

给Codex一个明确请求即可，例如：

> 按本项目的基地车框架创建长期协作岗位，所有岗位共用当前项目目录，使用默认research编制。项目目标是：……。完成环境检查和岗位初始化后，由主控继续处理这个目标。

安装协调者按下面的实际状态执行，普通交接不反复询问已明确的授权：

1. 读当前项目README和AGENTS，确认用户目标、业务域和写入范围。默认每域research共13岗，compact共5岗。文件已安装时不重复覆盖。运行`python .agents/basecamp/run.py doctor --live`。
2. 确认当前对话运行在Codex桌面、Python>=3.11、CLI和桌面队列能力通过。未通过时修复具体依赖或报告能力缺口；不能把目录已生成称作多对话已运行。默认不改全局认证、模型、权限或现有项目。
3. 使用实际可用的`list_projects`或`python .agents/basecamp/run.py projects`查当前目录对应projectId。项目还未被桌面登记时，由用户将这个文件夹打开为Codex项目；目录一致后继续。不要猜projectId。
4. 当前对话位于目标项目并负责首个业务域时，先将当前环境CODEX_THREAD_ID登记为该域主控：`python .agents/basecamp/run.py bind --role "Main / 主控" --thread-id 实际当前ID`。bind会核对真实线程目录；不能把其他项目的安装协调者绑定进来。这样用户已经说明的目标直接由当前主控接续，不要求重复交代。随后按已要求的共享目录方式运行`python .agents/basecamp/run.py provision --project-id 实际ID --apply --shared-workspace`，只创建未登记岗位，使用当前用户默认模型与权限，记录实际threadId。多域的其他主控由provision新建。
5. 创建结果不明或pending时，先查询桌面新任务，核对同项目、标题与创建时间。验证真实threadId后`bind --role "Main / 规划场1" --thread-id 实际ID`，再续provision。不要删provision.json重新开始，不复用其他项目地址，不恢复归档对话。
6. 用`status --live`观察岗位初始化，保留未就绪/需用户输入/失败的真实状态。模型/权限只有明确验证过才写observed。初始化回复不等于业务任务完成。
7. 全部必需岗位就绪后，当前已登记主控接续用户目标，按WORKFLOW派发；多域目标分别交本域主控。若特意另建主控而当前对话不在岗位名单，给用户明确主控入口，不冒充主控绕过身份检查。新目标仅在已授权范围内启动。

工具不暴露或版本不兼容时，仍可使用本项目技能和角色提示词由用户建立独立岗位，再用bind登记。不能用单轮子agent或伪造threadId冒充已建立的长期对话。

收发：`python .agents/basecamp/run.py send --to "Main / 规划场1" --file 任务目录/交接.md`。所有命令默认从项目根目录执行；在其他目录运行时添加`--root 项目路径`。
