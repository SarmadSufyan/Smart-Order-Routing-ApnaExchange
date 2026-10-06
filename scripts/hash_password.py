"""Make the .env line that gives a user a real login password.

    python scripts/hash_password.py admin        (or trader1, risk_mgr)

Asks for the new password twice (nothing shows while you type) and prints one
line to paste into .env, e.g.

    AUTH_PASSWORD_HASH_ADMIN=scrypt:16384:8:5:<salt>:<hash>

.env then holds only a scrypt hash - a fingerprint of the password that can't
be turned back into it. Restart the gateway; from then on that user needs this
password and the demo login (password = user name) stops working for them.
Delete the line to go back to the demo login. Background and options:
project-info/SECURITY_GUIDE.md.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from backend.gateway.middleware.auth import USERS
    from backend.gateway.middleware.passwords import hash_password, password_problem, verify_password
except ModuleNotFoundError as exc:  # a plain `python` on PATH, not the project's
    sys.exit(f"This Python has no '{exc.name}' package. Run the script with the project's Python:\n"
             "    .venv\\Scripts\\python.exe scripts\\hash_password.py <user>\n"
             "(or activate it once per terminal: .venv\\Scripts\\Activate.ps1, then python works)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Make the .env line for a real login password")
    parser.add_argument("user", choices=sorted(USERS), help="the user who gets the password")
    user = parser.parse_args().user

    password = getpass.getpass(f"New password for {user}: ")
    if problem := password_problem(user, password):
        print(f"Not used: {problem}.")
        return 1
    if getpass.getpass("Same password again: ") != password:
        print("The two passwords differ - nothing was made.")
        return 1

    line = f"AUTH_PASSWORD_HASH_{user.upper()}={hash_password(password)}"
    assert verify_password(password, line.split("=", 1)[1])  # the gateway will accept it
    print("\nPaste this ONE line into .env (replace an older AUTH_PASSWORD_HASH line for this user -")
    print("with the same name twice, the later line wins), then restart the gateway:\n")
    print(f"  {line}\n")
    print("It is a hash, not the password: it can't be turned back into it. Keep the password somewhere safe;")
    print("a forgotten one is replaced by running this script again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
