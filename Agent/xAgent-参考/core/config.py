"""配置读取。只读 ini，不认识界面。"""
import os

DEFAULT_BASE_URL = "http://127.0.0.1:11437/v1"
DEFAULT_TIMEOUT = 300


class ConfigError(Exception):
    """配置缺失或不合法。"""


class Config:
    """一份模型接入配置。"""

    def __init__(self, base_url, model, api_key="", timeout=DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = _clean_key(api_key)
        self.timeout = timeout
        self.sections = {}  # load() 会把解析好的小节塞进来

    @property
    def endpoint(self):
        """chat/completions 的完整地址。"""
        return self.base_url + "/chat/completions"

    @property
    def headers(self):
        """请求头，api_key 非空时带 Authorization。"""
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        return h

    def __repr__(self):
        return "Config(model=%r, base_url=%r)" % (self.model, self.base_url)


def _clean_key(api_key):
    """清洗 api_key。

    HTTP 头只能装 latin-1 字符，模板里的中文占位符（比如「你的密钥」）
    如果原样塞进 Authorization 会在发请求时抛 UnicodeEncodeError。
    这里把不是 ASCII 的密钥一律当作没填。
    """
    key = (api_key or "").strip().strip('"').strip("'")
    if not key:
        return ""
    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        return ""
    # 模板占位符也当没填
    if key.lower() in ("your-key", "your_key", "placeholder", "xxx", "none", "null"):
        return ""
    return key


def parse_ini(path):
    """解析 ini，返回 {小节名: {键: 值}}。

    顶层键（不在任何 [小节] 里的）归到空字符串这个名字下。
    键名统一小写，值保持原样（只去首尾空白）。

    [search] 这种小节必须保留层次：如果平铺成一个字典，
    baidu_qianfan_api_key 就会和 base_url 混在一起，读不出它属于哪个小节。
    """
    sections = {"": {}}
    current = ""
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip().lower()
                sections.setdefault(current, {})
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            sections.setdefault(current, {})[k.strip().lower()] = v.strip()
    return sections


def _candidate_paths(filename):
    """配置文件候选路径：当前目录优先，其次程序自己的目录。

    这样在任意工作目录下操作文件时，也还能找到配置。
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mine = os.path.join(here, filename)
    cwd = os.path.abspath(filename)
    if os.path.abspath(cwd) == os.path.abspath(mine):
        return [mine]
    return [cwd, mine]


def find_config(filename="config.ini"):
    """返回第一个存在的配置文件路径，都找不到返回 None。"""
    for path in _candidate_paths(filename):
        if os.path.isfile(path):
            return path
    return None


def load(filename="config.ini"):
    """读取配置文件，返回 Config。"""
    path = find_config(filename)
    if path is None:
        raise ConfigError(
            "找不到 %s。请把它放在当前目录或程序目录下，格式见 config.example.ini。" % filename
        )

    sections = parse_ini(path)
    raw = sections.get("", {})

    if not raw.get("base_url"):
        raise ConfigError("%s 里缺少 base_url。" % path)
    if not raw.get("model"):
        raise ConfigError("%s 里缺少 model。" % path)

    timeout = DEFAULT_TIMEOUT
    if raw.get("timeout"):
        try:
            timeout = int(raw["timeout"])
        except ValueError:
            timeout = DEFAULT_TIMEOUT

    cfg = Config(
        base_url=raw["base_url"],
        model=raw["model"],
        api_key=raw.get("api_key", ""),
        timeout=timeout,
    )
    cfg.sections = sections
    return cfg


def read_section(section, key, filename="config.ini"):
    """从指定小节读一个配置项，读不到返回空串。

    工具的配置（比如 [search] 的 baidu_qianfan_api_key）走这里，
    不要硬编码在代码里。
    """
    path = find_config(filename)
    if path is None:
        return ""
    sections = parse_ini(path)
    return sections.get(section.lower(), {}).get(key.lower(), "")


def load_or_default(filename="config.ini"):
    """读配置，读不到就返回一份默认配置，让界面还能起来去报错提示。"""
    try:
        return load(filename)
    except ConfigError:
        return Config(DEFAULT_BASE_URL, "")
