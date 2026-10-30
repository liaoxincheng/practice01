# 宠物医院 MCP Server

把宠物医院 REST API 包装成 **MCP 2026-07-28** 工具,让 AI 能查档案、建档案、写病历,
并基于历史病历生成随访小结。实现按 [DEV_PROMPT.md](../DEV_PROMPT.md) 的提示词完成,
在 MVP 的 `list_pets` 之上补齐了其余 4 个工具。

## 工具(5 个)

| 工具 | 作用 | 主要参数 |
| --- | --- | --- |
| `list_pets` | 查询档案列表:筛选 + 全文检索 + 排序 + 分页 | `species`、`doctor`、`status`、`search`、`owner_name`、`owner_phone`、`disease`、`min_cost`、`max_cost`、`sort_by`、`order`、`page`、`page_size` |
| `create_pet` | 新建宠物档案 | 必填 `name`、`species`、`owner_name`、`owner_phone`、`disease`、`doctor` |
| `get_pet` | 按 ID 查完整档案(含病历与消费) | `pet_id`(如 `PET-000001`) |
| `add_record` | 追加一条历史病历 | 必填 `pet_id`、`diagnosis`;可选医生、症状、处置、处方、复查建议、费用等 |
| `followup_summary` | 用本机 Ollama 生成三段式随访小结 | `pet_id` |

所有工具的参数都由类型提示自动生成 JSON Schema;参数错误、后端报错、模型服务不可用
都会以 MCP 错误结果返回,不会让服务器崩溃。

## 依赖的服务

| 服务 | 默认地址 | 说明 |
| --- | --- | --- |
| 宠物医院后端 | `http://127.0.0.1:8080` | 仓库里的 [backend/](../backend/) 是第三方 MIT 项目,负责数据存储与 REST API |
| Ollama | `http://127.0.0.1:11435`,模型 `qwen3-vl:4b-instruct` | 仅 `followup_summary` 需要;不启动时其余 4 个工具照常可用 |

启动后端(二选一):

```bash
# 有 Go 环境:在 backend/ 目录编译运行
cd ../backend && go run . -seed

# 或有发行版:直接运行 pethospital.exe(需与 data/pet.db 同目录)
```

## 安装

```bash
cd mcp-server
python -m venv .venv
.venv\Scripts\pip install -e .
```

## 配置(环境变量)

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PET_API_URL` | `http://127.0.0.1:8080` | 宠物医院后端地址 |
| `MCP_TRANSPORT` | `streamable-http` | `streamable-http` 或 `stdio` |
| `MCP_PORT` | `8081` | HTTP 模式端口,端点 `/mcp` |
| `OLLAMA_URL` | `http://127.0.0.1:11435` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen3-vl:4b-instruct` | 随访小结使用的模型 |

## 启动

```bash
# HTTP 模式(默认,便于 curl / MCP Inspector 调试)
python server.py

# stdio 模式(供 Qoder / opencode / Claude Desktop 等客户端拉起)
MCP_TRANSPORT=stdio python server.py
```

HTTP 模式调用示例:

```bash
curl -X POST http://127.0.0.1:8081/mcp \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: list_pets" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"list_pets","arguments":{"species":"犬","page_size":5}}}'
```

## 测试

```bash
.venv\Scripts\python -m unittest test_server -v
```

测试自带一个模拟宠物医院 + 模拟 Ollama 的本地 HTTP 服务,不依赖真实后端、Ollama 或网络,
覆盖:参数映射、分页/筛选透传、ID 越界防护、新建/追加病历、随访小结、错误处理,
并通过 MCP 层实际调用 5 个工具。

## 代码结构

```
pet_api_client.py   REST 客户端:响应信封拆解、ID 校验、查询参数映射
ollama_client.py    Ollama 客户端:病历渲染 + 三段式随访小结
tools.py            5 个 @mcp.tool 工具(参数校验 + 调客户端)
server.py           入口:按 MCP_TRANSPORT 选择 stdio / streamable-http
test_server.py      单元测试(内置模拟后端)
```
