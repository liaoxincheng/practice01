# 大模型应用开发课程作业

按课程进度组织的作业仓库,一个目录一次任务。

- **PetHospitalMCP** —— 宠爱 AI 病历管理系统:Go 后端存病历,Python MCP 服务器把它包装成工具给 AI 调用。
- **AnythingLLMMCP** —— 把 AnythingLLM 工作区的检索问答接成 MCP 工具。
- **AnythingLLMServer** —— 单文件网页上传文档,让 AnythingLLM 完成向量化。
- **Agent/Practice01** —— `chat.py`:OpenAI SDK 终端对话,流式打字机输出,每轮把全部聊天记录发给模型(聊天历史上下文)。
- **Agent/Practice02** —— 流式对话 + function calling,模型自己决定调用工具;按课程推进保留了几个阶段的快照(见下文)。
- **Agent/xAgent** —— 把 Practice02 改造成 `core` + `tui` 两层:业务逻辑不依赖界面,终端界面用 Textual;工具扩展到 14 个,新增命令行与联网能力。

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

MCP 相关项目(PetHospitalMCP、AnythingLLMMCP、AnythingLLMServer)的用法见各自目录内的 README。

## Agent/Practice02 的文件

按课程推进保留了几个阶段的快照:

- `chat-stream-tool-getDate.py` —— 只有 `getDate` 一个工具(调本地 `date` 命令取当前日期时间)。
- `chat-stream-tool-isLeapYear.py` —— 加上 `isLeapYear`(取模判断闰年)。
- `chat-stream-tool-fileOps.py` —— 加上文件操作工具,完整版共 12 个(2 个时间工具 + 10 个文件工具)。
- `chat.py` —— 完整版快照,共 12 个工具。
- `chat-stream-tool-fileOps.mmd` —— 程序架构的 Mermaid 图源(用 <https://mermaid.live> 打开 .mmd 可渲染 / 导出图片)。

## xAgent 的工具清单

文件操作(9 个,带工作目录沙箱):`read_file` / `write_file` / `edit_file` / `list_dir` / `get_file_type` / `create_directory` / `delete_directory` / `delete_file` / `rename_path`。

时间(2 个):`get_date`(调用本机 PowerShell 命令取当前时间,固定命令防注入)/ `is_leap_year`(闰年判断)。

联网与命令行(3 个):

- `RunCli` —— 在本机执行任意命令行程序。接收 `command`(程序名)和 `args`(参数数组),用 `subprocess.run([程序名] + 参数, shell=False)` 执行,返回退出码、标准输出、标准错误。默认 30 秒超时,输出超过 4000 字符会截断并标明原始长度。
- `fetchWeb` —— 用 `subprocess` 调本地 `curl` 抓网页 HTML,再用 `trafilatura` 提取正文转 Markdown。保留标题、段落、列表、表格、链接,去掉导航栏、广告、页脚。curl 走参数列表方式,不用 `shell=True`。
- `searchWeb` —— 调百度千帆 AI 搜索,返回搜索摘要和参考来源的标题、链接。API Key 从 `config.ini` 的 `[search]` 小节读 `baidu_qianfan_api_key`,不在代码里硬编码。

这些工具都把错误转成给模型看的文本,不抛异常打断对话:缺 Key、HTTP 错误、网络不可达、超时、正文提取失败、程序不存在、参数类型不对,各有对应的清晰提示。

## xAgent 的分层

```
Agent/xAgent/
├─ main.py        程序入口(组装层):配置 → LLM → 工具 → Agent → TUI
├─ run.bat        Windows 一键启动
├─ core/          核心业务层,不依赖任何 UI 库
│  ├─ agent.py    Agent 编排:聊天历史 + 流式对话 + 工具调用循环
│  ├─ config.py   配置读取(config.ini → 结构化配置对象)
│  ├─ events.py   事件协议:core 与 UI 之间唯一的"对话语言"
│  ├─ llm.py      LLM 客户端:抽象接口 + OpenAI 兼容流式实现
│  ├─ tools.py    工具注册中心
│  ├─ text_utils.py  共享小工具:字节解码、输出截断
│  ├─ file_ops.py    9 个文件工具(安全沙箱)
│  ├─ time_ops.py    get_date / is_leap_year
│  ├─ cli_ops.py     RunCli
│  └─ web_ops.py     fetchWeb / searchWeb
├─ tui/           Textual 终端界面,只做显示和输入
├─ tests/         52 个单元测试(不联网、不需要本地模型)
└─ docs/          界面截图与生成脚本
```

依赖方向:core 不导入 tui,也不依赖任何 UI 库;UI 单向依赖 core;组件都在 main.py 组装(依赖倒置),换界面或换模型服务时 core 不用改。

## 架构图与界面

- xAgent 界面截图:`Agent/xAgent/docs/xAgent界面截图.svg`(浏览器打开)。
- 界面截图生成脚本:`Agent/xAgent/docs/make_screenshot.py`(无头模式,不需要启动模型)。
