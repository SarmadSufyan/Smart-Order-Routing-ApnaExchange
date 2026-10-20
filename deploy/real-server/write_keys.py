"""Write exchange keys into the real-money server's keys.env - run as root by deploy.py, keys on stdin.

    sudo python3 write_keys.py <keys.env path> [--drop testnet|mainnet]   < lines NAME=value

The keys arrive on stdin (never on a command line, where `ps` would show them), are merged into keys.env (other
keys stay), and the file is written owner-only for root: the gateway receives them through systemd's
EnvironmentFile, and not even the service user can read the file. Only exchange key names are accepted.
Prints names and the last 4 characters, never a whole key. Standard library only (the system's python3).
"""

import os
import re
import sys
import tempfile

NAME = re.compile(r"^(BINANCE|BYBIT|OKX|GATE)_(TESTNET|MAINNET)_API_(KEY|SECRET|PASSPHRASE)$")


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
        sys.exit("usage: sudo python3 write_keys.py <keys.env> [--drop testnet|mainnet] < NAME=value lines")
    path = sys.argv[1]
    drop = sys.argv[3].upper() if len(sys.argv) == 4 and sys.argv[2] == "--drop" else None
    values = read(path)
    if drop:
        values = {k: v for k, v in values.items() if f"_{drop}_" not in k}
    for line in sys.stdin:
        name, sep, value = line.rstrip("\r\n").partition("=")
        if not sep:
            continue
        if not NAME.match(name):
            sys.exit(f"refused: {name!r} is not an exchange key name")
        value = value.strip()
        if not value or "'" in value or any(c.isspace() for c in value):
            sys.exit(f"refused: the value for {name} is empty or has a space or a quote")
        values[name] = value
    body = "".join(f"{k}='{v}'\n" for k, v in sorted(values.items()))
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".keys.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# Exchange API keys - root-only; the gateway gets them through systemd (EnvironmentFile).\n")
        f.write("# Written by deploy.py keys / go-live. Never copy this file anywhere.\n")
        f.write(body)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    for k, v in sorted(values.items()):
        print(f"   {k} ...{v[-4:]}")
    if not values:
        print("   (no keys)")


if __name__ == "__main__":
    main()
