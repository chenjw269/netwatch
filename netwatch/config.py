"""监测节奏。间隔可以在窗口里改，默认几分钟一轮。"""

INTERNET_HOST = "223.5.5.5"
PING_COUNT = 4
PING_WAIT_MS = 800
DEEP_PING_COUNT = 20
# 两轮开始之间的间隔。探测本身要几秒到十几秒。
DEFAULT_INTERVAL_SEC = 180
INTERVAL_CHOICES = (60, 180, 300, 600)
HTTP_TIMEOUT_SEC = 4

DOMESTIC_SITES = (
    ("百度", "https://www.baidu.com"),
    ("腾讯", "https://www.qq.com"),
)
FOREIGN_SITES = (
    ("Google", "https://www.gstatic.com/generate_204"),
    ("Cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
)

# Clash / mihomo 常见混合端口，系统代理没写明端口时才扫。
PROXY_PORTS = (7897, 7890, 7891, 7892, 7893, 10809, 10808, 1080)


def normalize_interval(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value in INTERVAL_CHOICES:
        return value
    return DEFAULT_INTERVAL_SEC


def interval_label(seconds: int) -> str:
    return f"{seconds // 60} 分钟"
