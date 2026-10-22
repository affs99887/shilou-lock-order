#!/usr/bin/env python3
"""从机场订阅（Clash / Mihomo 的 yaml）生成拾漏锁单专用的 Mihomo 配置。

软件只认 http / socks5 代理，机场节点大多是 ss、vmess 这类协议，要由 Mihomo 转一道：

- 每个节点一个本地端口（默认从 18001 起）：软件选「代理轮流（机场节点）」，把这些
  端口逐行填进去。哪个节点被 Apple 拦了、哪个连不上，软件能逐个记住并暂停。
- 另有一个端口（默认 17899）：每条新连接轮到下一个节点，给「隧道代理」模式用。

生成的配置含节点密码，不要外传；另生成一份只含本机端口的代理地址清单，可直接粘进软件。
脚本只打印节点名称和端口，不打印密码。需要 PyYAML：pip3 install pyyaml

用法：
    python3 scripts/apw-mihomo-config.py 订阅.yaml
    python3 scripts/apw-mihomo-config.py 订阅.yaml --exclude '阿根廷|俄罗斯'
然后运行：mihomo -d ~/.apw-mihomo -f apw-mihomo.yaml
"""

import argparse
import re
import sys

import yaml

# 机场常塞进来的「剩余流量」「套餐到期」之类假节点，以及免费、下载专用这类不稳定的节点。
SKIP = r"免费|下载专用|剩余|到期|过期|重置|官网|流量|套餐"
GROUP = "APW轮换"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("subscription", help="机场订阅 yaml 文件")
    parser.add_argument("--output", default="apw-mihomo.yaml", help="生成的 Mihomo 配置")
    parser.add_argument("--list", default="apw-代理地址.txt", help="粘进软件的代理地址清单")
    parser.add_argument("--exclude", default="", help="再排除名称匹配这个正则的节点")
    parser.add_argument("--first-port", type=int, default=18001, help="第一个节点的本地端口")
    parser.add_argument("--rotate-port", type=int, default=17899, help="轮换端口")
    args = parser.parse_args()

    with open(args.subscription, encoding="utf-8") as f:
        source = yaml.safe_load(f)

    skip = re.compile(SKIP + (f"|{args.exclude}" if args.exclude else ""))
    nodes = [p for p in source.get("proxies") or [] if not skip.search(str(p.get("name", "")))]
    if not nodes:
        print("订阅里没有可用节点", file=sys.stderr)
        return 1

    listeners = [
        {
            "name": f"apw-{i + 1:02d}",
            "type": "mixed",
            "port": args.first_port + i,
            "listen": "127.0.0.1",
            "proxy": p["name"],
        }
        for i, p in enumerate(nodes)
    ]
    config = {
        "mixed-port": args.rotate_port,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "proxies": nodes,
        "proxy-groups": [
            {
                "name": GROUP,
                # round-robin：每条新连接换下一个节点。不能用 consistent-hashing，
                # 那会让同一个网站一直走同一个节点。
                "type": "load-balance",
                "strategy": "round-robin",
                "proxies": [p["name"] for p in nodes],
                # 定时检查节点是否连得通，连不通的节点轮换时会被跳过。
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
                "lazy": False,
            }
        ],
        "listeners": listeners,
        "rules": [f"MATCH,{GROUP}"],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("# 拾漏锁单专用 Mihomo 配置，含节点密码，不要外传。\n")
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    with open(args.list, "w", encoding="utf-8") as f:
        f.write("\n".join(f"http://127.0.0.1:{item['port']}" for item in listeners) + "\n")

    print(f"{len(nodes)} 个节点，端口 {args.first_port}～{args.first_port + len(nodes) - 1}，"
          f"轮换端口 {args.rotate_port}")
    for item in listeners:
        print(f"  {item['port']}  {item['proxy']}")
    print(f"配置：{args.output}；代理地址清单：{args.list}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
