# practice02 - 带工具调用的流式对话程序

在 practice01(纯对话)的基础上,让本地大模型可以**主动调用工具**:本程序提供 3 个文件操作工具(fileOps),模型按需调用,工具执行结果再送回模型继续回答。

- **流式输出**:打字机效果,逐字显示
- **工具调用**:read_file 读文件 / write_file 写文件 / list_dir 列目录
- **工具调用循环**:模型一轮对话中可以多次调用工具,直到回答完为止
- **聊天历史上下文**:与 practice01 相同

## 环境要求

- Python 3
- LM Studio 本地运行中,已加载模型并启动服务器(默认 http://127.0.0.1:1234)

## 配置

编辑 `config.ini`(含密钥,已被 .gitignore 排除,不提交):

```ini
[llm]
base_url = http://127.0.0.1:1234/v1
model = qwen3.5-4b
api_key = DUMMY
```

## 运行

```bash
# 首次运行前,创建虚拟环境并安装 OpenAI 库(以后不用重复安装)
python -m venv venv
venv\Scripts\pip install openai

# 启动(程序与 config.ini 在同一目录)
venv\Scripts\python chat-stream-tool-fileOps.py
```

输入问题回车即可。可以试试让模型用工具,例如:

- `帮我把"你好,世界"写入 hello.txt,然后再读出来念给我听`
- `列出当前目录下有什么文件`

输入 `exit` 或 `退出` 结束对话。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `chat-stream-tool-fileOps.py` | 主程序(流式对话 + 工具调用 + 文件操作) |
| `config.ini` | 本地配置文件(含密钥,**不提交**) |
| `README.md` | 本说明 |

## 下一步

practice02 把对话、工具、界面全部写在了一个文件里。改造任务(见 `../xAgent/`)将其按**分层架构**拆分为 core(核心业务,与界面无关)+ tui(Textual 终端界面)。
