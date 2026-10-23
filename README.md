# practice01 - 与本地大模型对话的终端程序

一个使用 **OpenAI SDK** 编写的 Python 命令行对话程序,通过 `config.ini` 连接本地 **LM Studio** 大模型服务(千问 3.5-4B),支持:

- **非流式输出**:`python chat.py`
- **流式输出**(打字机效果):`python chat.py --stream`
- **循环交互**:持续对话,不用每说一句就重跑程序
- **聊天历史上下文**:对话中告诉它你的名字,再问"我叫什么名字?",它能记住

## 环境要求

- Python 3
- LM Studio 本地运行中,已加载模型并启动服务器(默认 http://127.0.0.1:1234)

## 配置

编辑 `config.ini`(注意:该文件含密钥,已被 .gitignore 排除,不会上传):

```ini
[llm]
base_url = http://127.0.0.1:1234/v1
model = qwen3.5-4b
api_key = DUMMY
```

本地模型不需要真实密钥,`api_key` 习惯上填 `DUMMY` 占位。

## 运行

```bash
# 首次运行前,创建虚拟环境并安装 OpenAI 库(以后不用重复安装)
python -m venv venv
venv\Scripts\pip install openai

# 非流式输出
venv\Scripts\python chat.py

# 流式输出
venv\Scripts\python chat.py --stream
```

输入问题回车即可,输入 `exit` 或 `退出` 结束对话。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `chat.py` | 主程序(OpenAI SDK,循环交互,带聊天历史) |
| `config.ini` | 本地配置文件(含密钥,**不提交**,需要自己创建/填写) |
| `.gitignore` | 排除 config.ini 与 venv |
| `README.md` | 本说明 |
