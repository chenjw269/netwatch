"""监测节奏。数字偏小会更灵敏，但会一直占着 ping。"""

INTERNET_HOST = "223.5.5.5"
PING_COUNT = 4
PING_WAIT_MS = 800
DEEP_PING_COUNT = 20
# 一轮 ping 大约 4 秒，醒来间隔再睡这么久，整体大约 20 秒一次。
CYCLE_GAP_SEC = 14
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
