"""RunCli 工具的单元测试。

逐条对照作业要求验证：
    传参方式是列表、shell=False、返回 stdout/stderr/退出码、
    超时处理、输出截断、异常情况。
"""
import sys

sys.path.insert(0, ".")

from core.tools import cli_tools
from core.tools.cli_tools import (
    MAX_OUTPUT_CHARS,
    MAX_TIMEOUT,
    decode_output,
    normalize_args,
    resolve_program,
    run_cli,
    truncate,
)

passed = 0
failed = []


def check(label, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("OK   %s" % label)
    else:
        failed.append(label)
        print("FAIL %s  %s" % (label, detail))


PY = "python"

print("=" * 70)
print("一、作业要求：返回 stdout / stderr / 退出码")
print("=" * 70)

r = run_cli(PY, ["--version"])
check("正常命令退出码为 0", "退出码：0" in r, r[:150])
check("输出含 Python 版本", "Python 3." in r, r[:200])
check("有标准输出段", "--- 标准输出 ---" in r)
check("有标准错误段", "--- 标准错误 ---" in r)
check("标注执行成功", "执行成功" in r)

# 非零退出码
r = run_cli(PY, ["-c", "import sys; sys.exit(3)"])
check("非零退出码被返回", "退出码：3" in r, r[:150])
check("非零时提示看 stderr", "执行失败" in r, r[:200])

# 往 stderr 写东西
r = run_cli(PY, ["-c", "import sys; sys.stderr.write('boom')"])
check("stderr 内容被捕获", "boom" in r, r[:200])

# stdout 和 stderr 同时有
r = run_cli(PY, ["-c", "import sys; print('out'); sys.stderr.write('err')"])
check("stdout 和 stderr 分别返回", "out" in r and "err" in r, r[:250])

print()
print("=" * 70)
print("二、作业要求：参数列表方式，shell=False")
print("=" * 70)

import inspect

src = inspect.getsource(cli_tools)
check("源码里写了 shell=False", "shell=False" in src)
check("没有 shell=True", "shell=True" not in src)

# 参数里的特殊字符不被 shell 解释
r = run_cli(PY, ["-c", "print('a&b|c>d<e')"])
check("特殊字符作为普通参数传入", "a&b|c>d<e" in r, r[:200])

# 带空格的参数整体传入，不被切开
r = run_cli(PY, ["-c", "print('hello world')"])
check("带空格的参数不被切分", "hello world" in r, r[:200])

print()
print("=" * 70)
print("三、args 参数规范化")
print("=" * 70)

a, e = normalize_args(None)
check("args 为 None 时返回空列表", a == [] and e is None, str(a))

a, e = normalize_args(["-a", "-b"])
check("列表原样返回", a == ["-a", "-b"], str(a))

a, e = normalize_args("-la")
check("字符串当成单个参数", a == ["-la"], str(a))

a, e = normalize_args([1, True])
check("非字符串元素转成字符串", a == ["1", "True"], str(a))

a, e = normalize_args([None, "x"])
check("None 元素被跳过", a == ["x"], str(a))

a, e = normalize_args({"k": "v"})
check("字典类型被拒绝", a is None and "必须是字符串数组" in (e or ""), str(e))

print()
print("=" * 70)
print("四、参数校验")
print("=" * 70)

r = run_cli()
check("缺 command 时报错", "缺少 command" in r, r[:100])

r = run_cli("")
check("空 command 时报错", "command 不能为空" in r or "缺少 command" in r, r[:100])

r = run_cli("   ")
check("纯空格 command 报错", "command 不能为空" in r or "缺少 command" in r, r[:100])

r = run_cli("nonexist_xyz_999", [])
check("不存在的程序有提示", "找不到程序" in r, r[:150])
check("提示里列出常见程序", "python" in r or "git" in r, r[:200])

print()
print("=" * 70)
print("五、作业要求：超时处理")
print("=" * 70)

r = run_cli(PY, ["-c", "import time; time.sleep(10)"], timeout=2)
check("超时被识别", "超时" in r, r[:150])
check("超时后退出码显示为超时终止", "超时终止" in r, r[:200])
check("超时也给说明", "已被终止" in r, r[:250])

r = run_cli(PY, ["-c", "import time; time.sleep(5)"], timeout=1)
check("短超时也能生效", "超时" in r, r[:120])

# 超时上限
r = run_cli(PY, ["--version"], timeout=99999)
check("超时超过上限被夹到 120", "退出码：0" in r, r[:120])

print()
print("=" * 70)
print("六、作业要求：长输出截断并标明")
print("=" * 70)

r = run_cli(PY, ["-c", "print('x' * 20000)"])
check("超长输出被截断", "已截断" in r, r[:200])
check("截断标明原始长度", "原始长度" in r, r[:300])
check("截断后总长度可控", len(r) < MAX_OUTPUT_CHARS * 3, str(len(r)))

t, cut, length = truncate("abc", 10)
check("短文本不截断", not cut and t == "abc" and length == 3)

t, cut, length = truncate("x" * 100, 10)
check("长文本被截断", cut and len(t) == 10 and length == 100, str((len(t), length)))

# stderr 超长也要截断
r = run_cli(PY, ["-c", "import sys; sys.stderr.write('e'*20000)"])
check("stderr 超长也截断", "已截断" in r, r[:200])

print()
print("=" * 70)
print("七、编码处理")
print("=" * 70)

check("UTF-8 解码", decode_output("中文".encode("utf-8")) == "中文")
check("GBK 解码", decode_output("中文".encode("gbk")) == "中文")
check("空字节返回空串", decode_output(b"") == "")
check("乱码不抛异常", isinstance(decode_output(b"\xff\xfe\x00\x01"), str))

# 真实中文命令
r = run_cli("cmd", ["/c", "echo 中文测试"])
check("中文输出不乱码", "中文测试" in r, r[:200])

print()
print("=" * 70)
print("八、程序查找")
print("=" * 70)

p, e = resolve_program(PY)
check("能找到 python", p is not None and e is None, str(p))

p, e = resolve_program("nonexist_abc_xyz")
check("找不到时报错", p is None and "找不到" in (e or ""), str(e))

p, e = resolve_program(r"C:\Windows\System32\cmd.exe")
check("绝对路径存在的程序能找到", p is not None, str(p))

p, e = resolve_program(r"C:\no\such\file.exe")
check("绝对路径不存在时报错", p is None, str(p))

print()
print("=" * 70)
print("九、真实场景")
print("=" * 70)

r = run_cli("cmd", ["/c", "dir"])
check("dir 能执行", "退出码：0" in r, r[:150])

r = run_cli("git", ["--version"])
check("git --version 能执行", "git version" in r.lower(), r[:150])

r = run_cli("curl", ["--version"])
check("curl --version 能执行", "curl" in r.lower(), r[:150])

# 命令成功但 stderr 有内容（警告）
r = run_cli(PY, ["-c", "import sys; print('ok'); sys.stderr.write('warning')"])
check("成功但有警告时两者都保留", "ok" in r and "warning" in r and "退出码：0" in r, r[:250])

print()
print("=" * 70)
print("十、工具声明")
print("=" * 70)

tool = cli_tools.build_tools()[0]
check("工具名是 RunCli", tool.name == "RunCli", tool.name)
check("required 只有 command", tool.required == ["command"], str(tool.required))
props = tool.parameters["properties"]
check("有 command 参数", "command" in props)
check("有 args 参数且是数组", props["args"]["type"] == "array", str(props.get("args")))
check("args 元素类型是 string", props["args"]["items"]["type"] == "string")
check("有 timeout 参数", "timeout" in props)
check("description 提到返回退出码", "退出码" in tool.description, tool.description[:100])
check("description 提到参数数组", "数组" in tool.description, tool.description[:100])

print()
print("=" * 70)
print("通过 %d 项，失败 %d 项" % (passed, len(failed)))
if failed:
    for f in failed:
        print("  失败:", f)
sys.exit(1 if failed else 0)
