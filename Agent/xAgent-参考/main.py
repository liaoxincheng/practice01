"""xAgent 入口。

用法：
    python main.py                 启动终端界面
    python main.py --tools         只列出工具，不进界面
    python main.py --check         检查配置和工具是否就绪
"""
import sys

from core.config import ConfigError, find_config, load
from core.tools import default_registry


def list_tools():
    reg = default_registry()
    print("共 %d 个工具：" % len(reg))
    for schema in reg.schemas():
        fn = schema["function"]
        print("  %-24s %s" % (fn["name"], fn["description"].split("。")[0]))
    return 0


def check():
    ok = True
    path = find_config()
    if path:
        print("[OK] 配置文件：%s" % path)
    else:
        print("[!!] 找不到 config.ini，界面能起来但发不出请求")
        ok = False

    try:
        cfg = load()
        print("[OK] 模型：%s" % cfg.model)
        print("[OK] 地址：%s" % cfg.endpoint)
    except ConfigError as e:
        print("[!!] %s" % e)
        ok = False

    reg = default_registry()
    print("[OK] 工具：%d 个" % len(reg))
    return 0 if ok else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--tools" in argv:
        return list_tools()
    if "--check" in argv:
        return check()

    from tui.app import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
