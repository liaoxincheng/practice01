# Practice01 - 最简单的 LLM 对话程序

一个最小化的 Python 命令行对话程序，通过 config.ini 配置连接本地 Ollama 大模型，支持流式打字机输出。

## 环境要求

- Python 3
- Ollama 本地运行中，已拉取模型

## 配置

编辑 `config.ini`：

```ini
[llm]
base_url = http://localhost:11434
model = qwen3-vl:4b-instruct
```

## 运行

```bash
python chat.py
```

输入问题后回车，AI 回复会以打字机效果逐字输出。

## 文件说明

| 文件 | 作用 |
|------|------|
| `chat.py` | 主程序，读取 config.ini，流式调用 Ollama API |
| `config.ini` | LLM 地址和模型名配置 |
