"""Write keys into the real-money server's keys.env - run as root by deploy.py, keys on stdin.

    sudo python3 write_keys.py <keys.env path> [--drop testnet|mainnet]   < lines NAME=value
    sudo python3 write_keys.py <keys.env path> --remove NAME              (one key out, e.g. the copilot's)

The keys arrive on stdin (never on a command line, where `ps` would show them), are merged into keys.env (other
keys stay), and the file is written owner-only for root: the gateway receives them through systemd's
EnvironmentFile, and not even the service user can read the file. Only exchange key names, the phone alerts'
Telegram token and chat, and the copilot's Claude key (decision A5) are accepted.
Prints names and the last 4 characters, never a whole key. Standard library only (the system's python3).
"""

import os
import re
import sys
import tempfile

NAME = re.compile(r"^((BINANCE|BYBIT|OKX|GATE|HTX)_(TESTNET|MAINNET)_API_(KEY|SECRET|PASSPHRASE)|TELEGRAM_BOT_TOKEN"
                  r"|TELEGRAM_CHAT_ID|COPILOT_CLAUDE_API_KEY)$")


def read(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, sep, value = line.strip().partition("=")
                if sep and NAME.match(name):
                    values[name] = value.strip().strip("'")
    return values


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: sudo python3 write_keys.py <keys.env> [--drop testnet|mainnet | --remove NAME] "
                 "< NAME=value lines")
    path = sys.argv[1]
    drop = sys.argv[3].upper() if len(sys.argv) == 4 and sys.argv[2] == "--drop" else None
    remove = sys.argv[3] if len(sys.argv) == 4 and sys.argv[2] == "--remove" else None
    if remove is not None and not NAME.match(remove):
        sys.exit(f"refused: {remove!r} is not a key name this file holds")
    values = read(path)
    if drop:
        values = {k: v for k, v in values.items() if f"_{drop}_" not in k}
    if remove:
        values.pop(remove, None)
    for line in sys.stdin:
        name, sep, value = line.rstrip("\r\n").partition("=")
        if not sep:
            continue
        if not NAME.match(name):
            sys.exit(f"refused: {name!r} is not an exchange key (or Telegram, or the copilot's Claude key) name")
        value = value.strip()
        if not value or "'" in value or any(not 0x21 <= ord(c) <= 0x7E for c in value):
            # 0x21-0x7E: printable, no space - a stray keystroke (Ctrl+V = 0x16, found live) is never part of a key
            sys.exit(f"refused: the value for {name} is empty or has a space, a quote or an invisible character")
        values[name] = value
    body = "".join(f"{k}='{v}'\n" for k, v in sorted(values.items()))
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".keys.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# API keys (exchanges, phone alerts, the copilot's AI) - root-only; the gateway gets them through\n")
        f.write("# systemd (EnvironmentFile). Written by deploy.py keys / telegram / ai-key / go-live. Never copy it.\n")
        f.write(body)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    for k, v in sorted(values.items()):
        print(f"   {k} ...{v[-4:]}")
    if not values:
        print("   (no keys)")


if __name__ == "__main__":
    main()
