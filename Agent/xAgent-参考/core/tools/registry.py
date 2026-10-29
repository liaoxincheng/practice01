"""工具注册表与分发。

每个工具用一个 Tool 对象描述：名字、给模型看的说明、参数 schema、实现函数。
新增工具只要在这里 register 一下，core 和界面都不用改。
"""
import json


class Tool:
    """一个可被模型调用的工具。"""

    def __init__(self, name, description, func, parameters=None, required=None):
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.required = required or []

    def schema(self):
        """转成 OpenAI 兼容格式的工具声明。"""
        params = dict(self.parameters)
        params["required"] = self.required
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": params,
            },
        }

    def call(self, **kwargs):
        return self.func(**kwargs)


class Registry:
    """工具注册表。"""

    def __init__(self):
        self._tools = {}
        self._order = []

    def register(self, tool):
        if tool.name in self._tools:
            raise ValueError("工具重名：%s" % tool.name)
        self._tools[tool.name] = tool
        self._order.append(tool.name)
        return tool

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return list(self._order)

    def __len__(self):
        return len(self._tools)

    def __contains__(self, name):
        return name in self._tools

    def schemas(self):
        """全部工具声明的列表，直接塞进请求体。"""
        return [self._tools[n].schema() for n in self._order]

    def describe(self):
        """给人看的一句话清单，界面上可以显示。"""
        return "、".join(self._order)

    def execute(self, name, arguments):
        """执行工具。arguments 可以是 dict 或 JSON 字符串。

        返回 (文本结果, 错误信息)。错误信息为 None 表示正常。
        任何异常都转成文本反馈给模型，不让程序崩掉。
        """
        tool = self._tools.get(name)
        if tool is None:
            return None, "未知工具：%s。可用工具：%s" % (name, self.describe())

        if isinstance(arguments, str):
            text = arguments.strip() or "{}"
            try:
                kwargs = json.loads(text)
            except json.JSONDecodeError as e:
                return None, "工具 %s 的参数不是合法 JSON：%s。收到的内容：%s" % (name, e, text)
        else:
            kwargs = dict(arguments or {})

        if not isinstance(kwargs, dict):
            return None, "工具 %s 的参数必须是 JSON 对象。" % name

        try:
            return tool.call(**kwargs), None
        except TypeError as e:
            return None, "工具 %s 的参数不对：%s" % (name, e)
        except Exception as e:  # 工具内部任何异常都当作工具失败
            return None, "工具 %s 执行出错：%s: %s" % (name, type(e).__name__, e)
