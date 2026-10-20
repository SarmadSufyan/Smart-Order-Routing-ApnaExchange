#!/usr/bin/env bash
# Is this server set up safely for its stage? install_release.sh runs it before every switch, deploy.py go-live
# before turning real money on. As root:   sudo bash check_settings.sh [release folder] [live|rehearsal]
#   rehearsal (no shared/live yet): EXCHANGE_MODE=testnet, sandbox keys. Real keys may already be staged in
#             keys.env (typed for the read-only check, D3); a testnet gateway never reads them.
#   live:     EXCHANGE_MODE=mainnet + ALLOW_MAINNET=true, real keys only (no sandbox key left), a real password
#             for every user, nothing resumes by itself.
# It imports the whole gateway with this server's settings and keys, so a typo stops here, not at the switch.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lab-server/versions.env"
source "$HERE/lib.sh"
cd /

[[ $EUID -eq 0 ]] || die "run as root: sudo bash $0"
REL="${1:-$BASE/current}"
STAGE="${2:-$(stage)}"
[[ -d "$REL/backend" ]] || die "no release at $REL"
[[ -f "$BASE/shared/real.env" ]] || die "no $BASE/shared/real.env yet: deploy.py env"

cd "$REL"
as_app_with_keys env STAGE="$STAGE" "$REL/.venv/bin/python" - <<'EOF'
import os
import sys

from backend.gateway.config import settings
import backend.gateway.main  # noqa: F401  (every module imports with these settings)
from backend.gateway.middleware.auth import USERS

stage = os.environ["STAGE"]
keys = {k for k, v in os.environ.items() if k.endswith(("_API_KEY", "_API_SECRET", "_API_PASSPHRASE")) and v.strip()}
testnet_keys, real_keys = sorted(k for k in keys if "_TESTNET_" in k), sorted(k for k in keys if "_MAINNET_" in k)
problems = []

def need(ok: bool, why: str) -> None:
    if not ok:
        problems.append(why)

if stage == "live":
    need(settings.exchange_mode == "mainnet" and settings.allow_mainnet,
         "live needs EXCHANGE_MODE=mainnet and ALLOW_MAINNET=true in real.env (deploy.py go-live writes them)")
    need(not testnet_keys, f"sandbox keys are still on a live server: {', '.join(testnet_keys)}")
    traded = [e for e in settings.enabled_exchange_list if settings.exchange_credentials(e)]
    need(bool(traded), "no exchange has real keys: deploy.py keys")
    need(not settings.lab_auto_resume, "LAB_AUTO_RESUME must be off on real money")
    if not settings.mainnet_require_ip_lock:
        print("   WARNING: MAINNET_REQUIRE_IP_LOCK=false - keys without an IP lock will trade")
    caps = (settings.mainnet_max_order_usdt, settings.mainnet_max_algo_usdt, settings.mainnet_max_exposure_usdt,
            settings.mainnet_max_trading_loss_usdt)
    summary = (f"LIVE - real money on {', '.join(traded) or 'nothing'}; caps ${caps[0]:g} per order, ${caps[1]:g} "
               f"per algo, ${caps[2]:g} exposure, ${caps[3]:g} loss limit; cycle bots "
               f"{'ON' if settings.mainnet_cycle_bots else 'off'}; copilot AI {settings.llm_provider}")
else:
    need(settings.exchange_mode == "testnet" and not settings.allow_mainnet,
         "a rehearsal runs on the sandbox: EXCHANGE_MODE=testnet, ALLOW_MAINNET=false")
    traded = [e for e in settings.enabled_exchange_list if settings.exchange_credentials(e)]
    staged = f"; real keys staged for go-live: {len(real_keys)}" if real_keys else ""
    summary = f"REHEARSAL - sandbox keys for {', '.join(traded) or 'nothing'}{staged}"
missing = [u for u in sorted(USERS) if settings.password_hash_for(u) is None]
need(not missing, f"no real login password for {', '.join(missing)} (deploy.py passwords, then deploy.py env)")
if problems:
    print("   REFUSED:")
    for p in problems:
        print(f"   - {p}")
    sys.exit(1)
print(f"   {summary}")
EOF
