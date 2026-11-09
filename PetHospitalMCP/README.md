# PetHospitalMCP — 宠物医院 MCP

课程评分清单第 1 项。本目录由两部分组成:

| 目录 | 说明 | 许可 |
| --- | --- | --- |
| `backend/` | 宠物医院 REST 后端,提供病历数据存储与查询接口 | **第三方 MIT 代码**(Copyright (c) 2026 atfa),保留其 `LICENSE`,未修改 |
| `mcp-server/` | 本仓库原创的 Python MCP 服务器,把后端接口包装成 5 个 MCP 工具 | 本仓库原创 |

## MCP 工具(5 个)

`list_pets`(查询/筛选/分页)、`create_pet`(新建档案)、`get_pet`(按 ID 查完整档案)、
`add_record`(追加病历)、`followup_summary`(用 Ollama 生成三段式随访小结)。

详细参数、环境变量和调试方法见 [mcp-server/README.md](mcp-server/README.md)。

## 启动步骤

```bash
# 1. 启动后端(端口 8080)
cd backend
go run . -seed            # 或运行发行版 pethospital.exe

# 2. 安装并启动 MCP 服务器(端口 8081,端点 /mcp)
cd ../mcp-server
python -m venv .venv
.venv\Scripts\pip install -e .
python server.py

# 可选:stdio 模式(供 opencode / Claude Desktop 拉起)
$env:MCP_TRANSPORT = "stdio"
python server.py
```

`followup_summary` 还需要本机 Ollama(`http://127.0.0.1:11435`,模型
`qwen3-vl:4b-instruct`);不启动 Ollama 时,其余 4 个工具仍可正常使用。

## 测试

```bash
cd mcp-server
.venv\Scripts\python -m unittest test_server -v
```

测试使用内置模拟后端和模拟 Ollama,不需要真正启动服务。当前 **11 个测试全部通过**。

## 接入 xAgent

xAgent 的 `config.ini` 默认已配置:

```ini
[mcp]
servers = pet=http://127.0.0.1:8081/mcp, anythingllm=http://127.0.0.1:8082/mcp
```

启动 xAgent 后会自动发现这 5 个工具(名称形如 `mcp_pet_list_pets`)。
