# xAgent — 分层架构的本地大模型 Agent(Textual TUI)

基于 practice02(`chat-stream-tool-fileOps.py`)重构改造而来。改造目标:**把"核心业务逻辑"与"界面"彻底分离**,按软件工程的分层架构与依赖倒置原则,拆成 `core`(核心业务)+ `tui`(终端界面)两大模块,并为后续新增 Web 界面、桌面界面预留扩展能力。

## 一、改造前后对比(功能对照表)

| practice02 原有功能 | xAgent 中的位置 | 说明 |
| --- | --- | --- |
| 流式对话(打字机效果) | [core/llm.py](core/llm.py) 产流 + [tui/app.py](tui/app.py) 渲染 | 完整保留 |
| 循环交互 + 聊天历史上下文 | [core/agent.py](core/agent.py) 维护历史 | 完整保留 |
| 工具调用:read_file | [core/file_ops.py](core/file_ops.py) | 完整保留,新增**路径安全沙箱**(只能读写工作目录内文件) |
| 工具调用:write_file | [core/file_ops.py](core/file_ops.py) | 完整保留 |
| 工具调用:list_dir | [core/file_ops.py](core/file_ops.py) | 完整保留,新增文件/目录类型标注 |
| Practice02 文件工具(其余 9 个) | [core/file_ops.py](core/file_ops.py) | get_file_type / edit_file(追加、覆盖)/ create_directory / delete_directory(非空可删)/ delete_file / rename_path,全部带沙箱保护 |
| Practice02 时间工具 | [core/time_ops.py](core/time_ops.py) | get_date(调用本机 PowerShell 命令取时间,固定命令防注入)、is_leap_year(纯 Python) |
| 工具调用循环(多轮工具调用) | [core/agent.py](core/agent.py) | 完整保留,轮数上限可配置 |
| `exit` / `退出` 结束对话 | [tui/app.py](tui/app.py) | 完整保留,新增 `reset` 清空对话、`Ctrl+Q` 退出、`Ctrl+L` 清屏 |
| config.ini 连接本地模型 | [core/config.py](core/config.py) | 保留,配置项带默认值,更健壮 |
| —(新增)命令行工具 RunCli | [core/cli_ops.py](core/cli_ops.py) | 模型可执行本地命令:退出码+stdout+stderr 返回,超时控制、输出截断、shell 注入防护 |
| —(新增)联网抓取 fetchWeb | [core/web_ops.py](core/web_ops.py) | 模型可抓取网页并提取正文为 Markdown:保留标题/段落/列表/表格/链接,去除导航/广告/页脚 |
| —(新增)联网搜索 searchWeb | [core/web_ops.py](core/web_ops.py) | 模型可调用百度千帆 AI 搜索 API,返回搜索摘要+来源标题+URL |

> 工具总计 **14 个**:9 个文件工具 + 2 个时间工具 + 1 个命令行工具 + 2 个联网工具。

## 二、项目目录结构

```
xAgent/
├── main.py                  # 程序入口:组装 core 组件(依赖注入),启动 TUI
├── run.bat                  # Windows 一键启动(自动建虚拟环境、装依赖、启动)
├── config.ini               # 配置文件(模型/工具参数/千帆 API Key;含密钥不入库)
├── requirements.txt         # 依赖清单(openai、textual、httpx、trafilatura)
├── .gitignore               # 排除 venv、config.ini、workspace
├── README.md                # 本文档
│
├── core/                    # ★ 核心业务层:与 UI 完全无关,可独立复用
│   ├── __init__.py          #   包说明文档
│   ├── config.py            #   配置加载:config.ini → 结构化配置对象
│   ├── events.py            #   事件协议:core 与 UI 之间唯一的"对话语言"
│   ├── llm.py               #   LLM 客户端:抽象接口 LLMClient + OpenAI 兼容实现(流式)
│   ├── tools.py             #   工具注册中心:工具的注册、说明生成、执行
│   ├── text_utils.py        #   共享小工具:字节解码(UTF-8/GBK 自适应)、输出截断标注
│   ├── file_ops.py          #   文件操作工具(9 个):read/write/edit/create/delete/rename 等,带安全沙箱
│   ├── time_ops.py          #   时间工具:get_date(本机命令取时间)、is_leap_year(闰年判断)
│   ├── cli_ops.py           #   命令行工具:RunCli(执行本地命令,超时/截断/注入防护)
│   ├── web_ops.py           #   联网工具:fetchWeb(curl 抓网页+trafilatura 提取正文)、
│   │                        #   searchWeb(百度千帆 AI 搜索)
│   └── agent.py             #   Agent 编排核心:聊天历史 + 流式对话 + 工具调用循环
│
├── tui/                     # ★ 界面层:只负责显示和输入,不写业务逻辑
│   ├── __init__.py          #   包说明文档
│   ├── app.py               #   Textual 聊天界面:布局、事件流渲染、快捷键
│   └── chat.css             #   界面样式
│
├── tests/                   # 单元测试(不联网、不需要 LM Studio,使用假 LLM / 本地 HTTP 服务)
│   ├── test_core.py         #   core 层测试:流式回复、工具调用、错误回滚、路径越界防护
│   ├── test_extra_ops.py    #   时间工具 + 新增文件工具测试:取时间/闰年/编辑/删除/重命名/沙箱
│   ├── test_cli_ops.py      #   RunCli 测试:执行/超时/截断/注入防护/schema/Agent 集成
│   ├── test_web_ops.py      #   fetchWeb/searchWeb 测试:抓取/提取/404/超时/鉴权/schema/Agent 集成
│   └── test_tui_smoke.py    #   TUI 冒烟测试:Textual 无头模式验证界面挂载与渲染
│
└── docs/                    # 文档素材
    ├── make_screenshot.py   #   界面截图生成脚本(无头模式,不需要 LM Studio)
    └── xAgent界面截图.svg   #   界面截图(浏览器可打开,可转图片放进作业报告)
```

## 三、架构设计

### 3.1 分层与依赖方向(核心约束)

```
┌─────────────────────────────────────────────────┐
│                 界面层 (tui/)                     │
│   Textual 界面:渲染 / 接收输入 / 快捷键           │
│   (未来可新增 web/、desktop/ 包,互不影响)          │
└──────────────────────┬──────────────────────────┘
                       │ 只调用 ↓ 只消费事件流
┌──────────────────────▼──────────────────────────┐
│               核心业务层 (core/)                   │
│  Agent 编排 ──► LLMClient 抽象 ──► 流式调用        │
│      │                                         │
│      └──► ToolRegistry ◄── 注册:文件/命令行/联网工具 │
└─────────────────────────────────────────────────┘

依赖规则:
  · core 不 import 任何 tui 代码,也不依赖任何 UI 库;
  · UI 层单向依赖 core;
  · core 可以单独被任何界面复用(命令行、Web、桌面、测试)。
```

### 3.2 关键设计

1. **依赖倒置(DIP)**
   - `core` 里的 `Agent` 只依赖抽象接口 `LLMClient`、`ToolRegistry`,不依赖具体实现;
   - 测试时注入 `FakeLLM`(假大模型)即可完整验证业务逻辑,不联网、不需要 LM Studio;
   - 换模型服务(OpenAI / Ollama / 云端 API)只需新增一个 `LLMClient` 实现类。

2. **事件流解耦**
   - core 与 UI 之间不互相调用,而是通过 [core/events.py](core/events.py) 定义的标准事件通信;
   - `Agent.chat()` 是一个**异步生成器**,UI 只需 `async for event in agent.chat(...)` 遍历事件流渲染;
   - 因此任何界面技术(Textual / Web / 桌面)都能接入同一套 core。

3. **组装层(Composition Root)**
   - 组件创建与连接的代码集中在 [main.py](main.py),业务模块之间不自己 new 对象;
   - 新增界面时只改 main.py 最后几行,core 一行都不用动。

4. **安全沙箱**
   - 文件工具的所有路径都被限制在配置的 `work_dir` 内,越界路径(`../`、绝对路径出界)直接拒绝,防止模型读写任意系统文件。

## 四、运行说明

### 环境要求

- Python 3.10 及以上
- LM Studio 本地运行中,已加载模型并启动服务器(默认 http://127.0.0.1:1234)
- fetchWeb 需要系统自带 curl(Windows 10/11 已自带,无需额外安装)
- searchWeb(可选)需要百度千帆 API Key,填入 config.ini 的 [search] 段(不填也不影响其他功能)

### 方式一:一键启动(推荐)

双击 [run.bat](run.bat) —— 自动完成"创建虚拟环境 → 安装依赖 → 启动程序"。

### 方式二:手动命令

```bash
cd xAgent

# 1. 首次运行:创建虚拟环境并安装依赖(以后不用重复)
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# 2. 启动
venv\Scripts\python main.py
```

### 配置

编辑 [config.ini](config.ini)(含密钥,已被 .gitignore 排除;拿到代码的同学/老师需自行创建,内容见文件内注释):

```ini
[llm]
base_url = http://127.0.0.1:1234/v1   ; LM Studio 的 OpenAI 兼容接口
model = qwen3.5-4b
api_key = DUMMY

[agent]
max_tool_rounds = 5                   ; 工具调用最大来回数

[file_ops]
work_dir = workspace                  ; 模型只能读写这个目录内的文件

[web_ops]
fetch_timeout_seconds = 20            ; fetchWeb 下载单个网页的超时(秒)
max_content_chars = 6000              ; 网页正文/搜索结果的最大保留字符数(超出截断)

[search]
; searchWeb 的百度千帆 API Key —— 需要在百度智能云千帆平台申请后填写,
; 不填则该工具返回"未配置"提示,其余功能不受影响
baidu_qianfan_api_key =
top_k = 10                            ; 每次搜索返回的条数
timeout_seconds = 20                  ; 搜索 API 请求超时(秒)
```

### 使用

- 输入消息回车发送,AI 流式回复;
- 试试让模型用文件工具:`帮我把"你好,世界"写入 hello.txt,再读出来念给我听`、`列出目录下有什么文件`、`在 notes.txt 末尾追加一行"备忘"`、`把 hello.txt 重命名为 greet.txt`、`建一个 projects/demo 目录`、`hello.txt 是什么类型的文件`;
- 试试让模型用时间工具:`现在几点了`、`2024 年是闰年吗`;
- 试试让模型用命令行工具 RunCli:`ping 一下 127.0.0.1 看通不通`、`用 ipconfig 查一下我的 IP 地址`;
- 试试让模型联网抓网页:`用 fetchWeb 抓取 https://example.com 并总结一下内容`;
- 试试让模型联网搜索:`用 searchWeb 搜索"2026 年诺贝尔物理学奖得主"`(需先在 config.ini 的 [search] 段填好百度千帆 API Key);
- `exit` / `退出` 结束程序,`reset` 清空对话,`Ctrl+Q` 退出,`Ctrl+L` 清屏;
- 模型写入的文件在工作目录 `workspace/` 里,可以直接打开看。

### 运行测试

```bash
venv\Scripts\python -m unittest discover -s tests -v
```

### 生成界面截图(用于作业报告)

```bash
venv\Scripts\python docs\make_screenshot.py
```

输出 [docs/xAgent界面截图.svg](docs/xAgent界面截图.svg)(演示了一次完整的"写文件 → 读文件"工具调用对话),用浏览器打开即可查看,也可截图转成图片放进报告。

## 五、扩展指南(预留的扩展点)

### 5.1 新增 Web 界面(示例:FastAPI + WebSocket)

core 一行不改,新增 `web/` 包:

```python
# web/app.py —— 伪代码示意
from core.agent import Agent
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/chat")
async def chat(ws: WebSocket, agent: Agent):          # agent 由 main.py 注入
    await ws.accept()
    while True:
        text = await ws.receive_text()
        async for event in agent.chat(text):          # 同一个 core 事件流
            await ws.send_json({"type": type(event).__name__, ...})
```

### 5.2 新增桌面界面(示例:Tkinter)

core 一行不改,新增 `desktop/` 包,用 `asyncio.run` 消费同一个事件流即可:

```python
# desktop/app.py —— 伪代码示意
import asyncio

async def _consume(agent, text, on_token, on_done):
    async for event in agent.chat(text):
        if isinstance(event, TextChunk):
            on_token(event.text)    # 更新文本框
        ...

def on_send(text):
    asyncio.run(_consume(agent, text, ...))            # 同步界面桥接异步 core
```

### 5.3 新增工具(照 web_ops.py 的样子)

本项目最近一次"新增工具"就是现成例子:[core/web_ops.py](core/web_ops.py) 一次注册了
fetchWeb、searchWeb 两个联网工具。照它的样子,在 core 里新增模块并 register 即可,
Agent 和界面都不用改:

```python
# core/xxx_ops.py —— 伪代码示意
from core.tools import Tool, ToolRegistry

def register_xxx_ops(registry: ToolRegistry, ...):    # 照 web_ops.py 的样子
    registry.register(Tool(name="xxx", description="……", parameters={...}, handler=...))
```

然后在 [main.py](main.py) 的 `build_agent()` 里加一行 `register_xxx_ops(tools, ...)` 即可。

## 六、常见问题

| 现象 | 原因与解决 |
| --- | --- |
| 回复显示 `❌ 调用大模型失败:Connection error` | LM Studio 没启动或没加载模型,启动 LM Studio 并确认服务器地址是 http://127.0.0.1:1234 |
| RunCli 执行时弹出"吊销服务器已经脱机" | Windows 对官网下载的 exe 做签名吊销检查,但连不上国外证书服务器。解决:用 run.bat 启动(已把 venv 的 python 放到 PATH 最前面,`python` 命令不再触发);其他程序仍弹窗时,打开「Internet 选项 → 高级 → 取消勾选"检查服务器证书吊销"」 |
| 模型执行 `dir` / `copy` 报"找不到程序" | 它们是 cmd 内置命令,不是独立程序(shell=False 的安全设计下无法执行);让模型改用 `powershell -Command "dir"` 或写 python 脚本实现 |
| fetchWeb 报"网页访问失败(curl exitcode = 22 / 28)" | 22 = 对方返回 404/403 等 HTTP 错误(网站拒绝访问,如百度百科反爬),28 = 超时。换一个网页再试;国内访问部分国外网站(如维基百科)超时属正常现象 |
| searchWeb 报"未配置百度千帆 API Key" | 按 [config.ini](config.ini) 的 [search] 段注释,在百度智能云千帆平台申请 API Key 并填入 `baidu_qianfan_api_key`,重启 xAgent |
| searchWeb 报"网络请求失败"或 code=xxx | 检查网络与 API Key 是否有效(千帆接口文档:https://cloud.baidu.com/doc/qianfan-api/s/Wmbq4z7e5 ) |
| 模型不调用工具 | 部分小模型对工具调用的支持有限,换用支持 function calling 的模型(如 qwen3 系列)再试 |
| 中文乱码 | 启动脚本已用 `chcp 65001` 处理;手动运行请在 Windows Terminal 中执行 |

### ⚠️ RunCli 安全说明(教学实验环境)

RunCli 会让模型以**当前用户权限**执行**任意本地命令**。本课程作业面向受控教学实验环境,暂不实现命令白名单/黑名单/审批机制。请注意:

- 不要把它暴露给不可信的用户输入或公开网络;
- 重要数据请先备份,谨慎让模型执行 `del`、`format` 等危险命令;
- 如需生产级安全,可在 `core/cli_ops.py` 的 `run_cli` 入口处增加命令白名单校验。
