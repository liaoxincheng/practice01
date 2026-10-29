# AnythingLLM MCP Server

一个用于访问AnythingLLM工作区的MCP服务器。

## 功能

- `query_workspace`: 查询AnythingLLM工作区并获取AI回答
- `list_workspaces`: 列出所有可用的工作区

## 安装

```bash
pip install -e .
```

## 配置

服务器默认连接 `http://localhost:3001`，使用提供的API密钥。

可以通过环境变量配置：
- `MCP_PORT`: MCP服务器端口（默认8082）
- `MCP_TRANSPORT`: 传输方式（默认streamable-http）

## 运行

```bash
# 使用streamable-http传输（默认）
python server.py

# 使用stdio传输
MCP_TRANSPORT=stdio python server.py
```

## 使用示例

查询工作区：
```python
await query_workspace(
    workspace_slug="your-workspace-slug",
    message="你的问题",
    mode="query"
)
```

列出工作区：
```python
await list_workspaces()
```

## 注意事项

1. 确保AnythingLLM服务正在运行
2. 验证API密钥是否正确
3. 检查工作区slug是否有效