# 大模型应用开发课程作业

按课程进度组织的作业仓库,一个目录一次任务。

- **PetHospitalMCP** —— 宠爱 AI 病历管理系统:`backend/` 是第三方 MIT 许可的 Go 后端(存储病历数据,保留其 LICENSE),`mcp-server/` 是本仓库原创的 Python MCP 服务器,把后端的 REST 接口包装成 5 个工具给 AI 调用。
- **AnythingLLMMCP** —— 把 AnythingLLM 工作区的检索问答接成 MCP 工具。
- **AnythingLLMServer** —— 单文件网页上传文档,让 AnythingLLM 完成向量化。
- **Agent/Practice01** —— `chat.py`:OpenAI SDK 终端对话,流式打字机输出,每轮把全部聊天记录发给模型(聊天历史上下文)。
- **Agent/Practice02** —— 流式对话 + function calling,模型自己决定调用工具;按课程推进保留了几个阶段的快照(见下文)。
- **Agent/xAgent** —— 把 Practice02 改造成 `core` + `tui` 两层:业务逻辑不依赖界面,终端界面用 Textual;内置工具扩展到 21 个(命令行、联网、技能发现与创建、工作区外只读、MCP 配置、上下文归档检索),并能把本仓库两个 MCP 服务器的工具(共 7 个)自动接入,全部接入后共 28 个工具;支持 Agent Skills 自动发现/按需加载、`.mcp.json`(HTTP + stdio)、统一系统提示词、`AGENTS.md` 项目说明(会话开始时注入一次,全程复用)、上下文压缩与会话归档(`/compact` 先归档再摘要、`recallContext` 检索旧细节、`/save` `/resume` 保存恢复),以及缺失 `config.ini` 时自动生成并弹窗提醒。

## 用法

Agent/Practice01、Agent/Practice02:复制 `config.example.ini` 为 `config.ini`,填好自己的接口地址、模型名和密钥,然后:

```bash
python chat.py
```

Agent/xAgent:同样复制 `config.example.ini` 为 `config.ini`,然后:

```bash
python main.py        # 或双击 run.bat(自动建虚拟环境、装依赖、启动)
```

xAgent 的 `core/` 不导入 textual 或任何界面框架,将来加 Web / Desktop 版只需再写一个界面层。

xAgent 的联网工具需要在 `config.ini` 的 `[search]` 小节填百度千帆的 API Key,`searchWeb` 才能用;`fetchWeb` 依赖 `trafilatura`,没装会给出安装提示,不影响其余工具。

xAgent 的 `[mcp]` 小节默认接入本仓库的两个 MCP 服务器(宠物医院 `127.0.0.1:8081/mcp`、AnythingLLM `127.0.0.1:8082/mcp`);启动它们后 xAgent 会自动发现并注册工具,没启动时自动跳过,不影响内置工具。也可以在启动目录放 `.mcp.json` 配置 HTTP / stdio MCP 服务器(支持 `${VAR}` 环境变量),或让模型调用 `createMcp` 写入。在启动 xAgent 的当前目录放一个 `AGENTS.md`,它的内容会在系统提示词之后注入聊天历史,整个会话只注入一次、之后每轮复用。对话太长时用 `/compact` 把旧消息压缩成摘要(压缩前自动归档完整历史),需要旧细节时模型用 `recallContext` 检索归档;`/save` `/sessions` `/resume` 负责手动保存与恢复会话。详见 [Agent/xAgent/README.md](Agent/xAgent/README.md)。

MCP 相关项目(PetHospitalMCP、AnythingLLMMCP、AnythingLLMServer)的用法见各自目录内的 README。

## Agent/Practice02 的文件

按课程推进保留了几个阶段的快照:

- `chat-stream-tool-getDate.py` —— 只有 `getDate` 一个工具(调本地 `date` 命令取当前日期时间)。
- `chat-stream-tool-isLeapYear.py` —— 加上 `isLeapYear`(取模判断闰年)。
- `chat-stream-tool-fileOps.py` —— 加上文件操作工具,完整版共 12 个(2 个时间工具 + 10 个文件工具)。
- `chat.py` —— 完整版快照,共 12 个工具。
- `chat-stream-tool-fileOps.mmd` —— 程序架构的 Mermaid 图源(用 <https://mermaid.live> 打开 .mmd 可渲染 / 导出图片)。

## xAgent 的工具清单

文件操作(10 个,带工作目录沙箱):`read_file` / `write_file` / `edit_file` / `list_dir` / `get_file_type` / `create_directory` / `delete_directory` / `delete_file` / `rename_path` / `get_current_directory`(返回当前工作目录)。

时间(3 个):`get_date`(调用本机 PowerShell 命令取当前时间,固定命令防注入)/ `get_current_datetime`(本地日期、时间、星期、时区与 UTC)/ `is_leap_year`(闰年判断)。

联网与命令行(3 个):

- `RunCli` —— 在本机执行任意命令行程序。接收 `command`(程序名)和 `args`(参数数组),用 `subprocess.run([程序名] + 参数, shell=False)` 执行,返回退出码、标准输出、标准错误。默认 30 秒超时,输出超过 4000 字符会截断并标明原始长度。
- `fetchWeb` —— 用 `subprocess` 调本地 `curl` 抓网页 HTML,再用 `trafilatura` 提取正文转 Markdown。保留标题、段落、列表、表格、链接,去掉导航栏、广告、页脚。curl 走参数列表方式,不用 `shell=True`。
- `searchWeb` —— 调百度千帆 AI 搜索,返回搜索摘要和参考来源的标题、链接。API Key 从 `config.ini` 的 `[search]` 小节读 `baidu_qianfan_api_key`,不在代码里硬编码。

技能(2 个):`createSkill` 按用户自然语言要求创建新的 Agent Skill:在 `.agents/skills/<名称>/` 下生成符合 Agent Skills 规范的 `SKILL.md`(YAML Frontmatter 的 name/description + 正文)。名称只允许小写字母、数字和连字符;已存在同名技能时拒绝创建、不覆盖。`loadSkill` 在任务需要时按需读取 SKILL.md 全文或技能目录内的附件(如 `references/guide.md`),只把技能名称和简介放进系统提示词,避免正文长期占用上下文。

工作区外只读(1 个):`readExternalFile` —— 读取 `work_dir` 之外的文件(如桌面资料),只读不写;拒绝二进制文件与设备路径,UTF-8 / GBK / UTF-16 自适应,超长内容截断并标注。

MCP 配置(1 个):`createMcp` —— 模型按自然语言把新 MCP 服务器写进 `.mcp.json`:校验 HTTP / stdio 配置、合并已有服务器、保留其他字段,同名默认拒绝(可显式覆盖),原子写入 + 回读校验,返回结果不泄露密钥。

上下文归档(1 个):`recallContext` —— 在 `/compact` 压缩前自动归档的会话里按关键字检索旧对话。上下文被压缩后,当前历史只剩摘要和最近几轮;用户提到早先的结论、路径或决定时,模型用这个工具找回原文片段(只读归档,不会修改当前对话)。

MCP 接入(动态):启动时连接 `config.ini` 的 `[mcp]` 段和 `.mcp.json` 配置的 MCP 服务器,把对方工具注册成 `mcp_<服务器>_<工具>`(如 `mcp_pet_list_pets`)。支持 HTTP(可带自定义请求头)与 stdio(`command`/`args`/`env`/`cwd`)两种传输,配置里可用 `${VAR}` / `${VAR:-默认值}` 引用环境变量。宠物医院 MCP 提供 5 个,AnythingLLM MCP 提供 2 个;服务器没启动时自动跳过并在欢迎语中说明原因。MCP 只是"发现 + 调用"的客户端逻辑,和内置工具走同一套 ToolRegistry,Agent 核心无需任何改动。

这些工具都把错误转成给模型看的文本,不抛异常打断对话:缺 Key、HTTP 错误、网络不可达、超时、正文提取失败、程序不存在、参数类型不对、技能名称不合法或已存在,各有对应的清晰提示。

## xAgent 的分层

```
Agent/xAgent/
├─ main.py        程序入口(组装层):配置 → LLM → 工具 → Agent → TUI
├─ run.bat        Windows 一键启动
├─ core/          核心业务层,不依赖任何 UI 库
│  ├─ agent.py    Agent 编排:聊天历史 + 流式对话 + 工具调用循环
│  ├─ config.py   配置读取 + 缺失 config.ini 自动生成
│  ├─ events.py   事件协议:core 与 UI 之间唯一的"对话语言"
│  ├─ llm.py      LLM 客户端:抽象接口 + OpenAI 兼容流式实现
│  ├─ tools.py    工具注册中心
│  ├─ text_utils.py  共享小工具:字节解码、输出截断
│  ├─ file_ops.py    10 个文件/目录工具(安全沙箱,含 get_current_directory)
│  ├─ time_ops.py    get_date / get_current_datetime / is_leap_year
│  ├─ cli_ops.py     RunCli
│  ├─ web_ops.py     fetchWeb / searchWeb
│  ├─ skill_ops.py   createSkill / loadSkill
│  ├─ skills_catalog.py 技能自动发现(SKILL.md 元数据、多根目录去重)
│  ├─ external_ops.py   readExternalFile(工作区外只读)
│  ├─ mcp_ops.py     MCP 客户端(HTTP / stdio,发现外部工具并注册)
│  ├─ mcp_config.py  .mcp.json 解析(传输推断、${VAR} 展开)
│  ├─ mcp_config_ops.py createMcp(写入 .mcp.json)
│  ├─ prompts.py     系统提示词(工作区 + 技能 + MCP 状态 + 归档检索)
│  ├─ agents_md.py   AGENTS.md 读取与一次性注入
│  ├─ context.py     Token 估算 + 会话归档(保存/列出/读取/检索)
│  └─ context_ops.py recallContext(在压缩前的归档里检索旧细节)
├─ .agents/skills/ Agent Skills 目录
├─ .mcp.json       可选:MCP 服务器配置
├─ .xagent/        会话归档目录(本地生成,已 gitignore)
├─ tui/           Textual 终端界面(斜杠命令、命令补全、Esc 取消)
├─ tests/         170 个单元测试(不联网、不需要本地模型)
└─ docs/          界面截图与生成脚本
```

依赖方向:core 不导入 tui,也不依赖任何 UI 库;UI 单向依赖 core;组件都在 main.py 组装(依赖倒置),换界面或换模型服务时 core 不用改。

## 架构图与界面

- xAgent 界面截图:`Agent/xAgent/docs/xAgent界面截图.svg`(浏览器打开)。
- 界面截图生成脚本:`Agent/xAgent/docs/make_screenshot.py`(无头模式,不需要启动模型)。
