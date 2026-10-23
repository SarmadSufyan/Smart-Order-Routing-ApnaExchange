"""What an exchange API key may do, as the exchange itself reports it (Phase D, real money).

ApnaSOR needs a key that can READ balances and TRADE spot. It must never be able to WITHDRAW: with a
withdrawal right, a leaked ``.env`` is lost money; without it, the worst a thief can do is trade. On real
money (``EXCHANGE_MODE=mainnet``) each adapter asks its exchange's read-only "key permissions" endpoint at
start and every few minutes, and a venue whose key can withdraw, can't trade spot, or whose answer doesn't
settle the question gets no orders (``CcxtVenueAdapter._watch_key_permissions``).

Each parser is a pure function over the exchange's JSON:

  Binance  GET /sapi/v1/account/apiRestrictions  enableWithdrawals, enableSpotAndMarginTrading, ipRestrict
  Bybit    GET /v5/user/query-api                readOnly, permissions.Spot has SpotTrade,
                                                 permissions.Wallet has Withdraw, ips, expiredAt
  OKX      GET /api/v5/account/config            perm = "read_only,trade[,withdraw]", ip
  Gate     GET /api/v4/account/main_keys         a read_only flag per area (spot, wallet, ...). Gate lists no
                                                 separate withdrawal flag, so a writable wallet area counts
                                                 as "may withdraw" — the strict reading.
  HTX      GET /v2/user/api-key (uid from        permission = "readOnly,trade[,withdraw]", ipAddresses, status,
           GET /v2/user/uid)                     validDays (-1 = never expires)

Anything a parser doesn't recognise is ``None`` ("the answer didn't say"), and ``None`` is never good enough
for real money.

The real-money server also requires each key to be **IP-locked** (``MAINNET_REQUIRE_IP_LOCK``, on by default):
the exchange accepts the key only from that server's fixed address, so a copied key is useless anywhere else
(Phase D plan, wall 3). The answer that settles it is the exchange's own IP list, read by the same parsers.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class KeyPermissions:
    can_trade_spot: bool | None  # None = the exchange's answer didn't say
    can_withdraw: bool | None
    ip_restricted: bool | None
    expires: str | None = None  # when the exchange disables the key by itself (keys without an IP list)
    summary: str = ""  # what the exchange said, in one line


def _yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _summary(trade: bool | None, withdraw: bool | None, ip: bool | None, extra: str = "") -> str:
    text = f"trade spot: {_yes_no(trade)}, withdraw: {_yes_no(withdraw)}, IP list: {_yes_no(ip)}"
    return f"{text}, {extra}" if extra else text


def parse_binance(raw: Any) -> KeyPermissions:
    """Binance ``GET /sapi/v1/account/apiRestrictions``."""
    data = raw if isinstance(raw, dict) else {}

    def flag(name: str) -> bool | None:
        value = data.get(name)
        return value if isinstance(value, bool) else None

    trade, withdraw, ip = flag("enableSpotAndMarginTrading"), flag("enableWithdrawals"), flag("ipRestrict")
    expires = None
    expiry_ms = data.get("tradingAuthorityExpirationTime")
    if isinstance(expiry_ms, int | float) and expiry_ms > 0:
        expires = datetime.fromtimestamp(expiry_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
    return KeyPermissions(trade, withdraw, ip, expires, _summary(trade, withdraw, ip))


def parse_bybit(raw: Any) -> KeyPermissions:
    """Bybit ``GET /v5/user/query-api`` (the whole answer, ``result`` inside)."""
    result = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get("permissions"), dict):
        return KeyPermissions(None, None, None, summary="the answer had no permissions")
    perms = result["permissions"]
    read_only = str(result.get("readOnly")) == "1"
    spot = "SpotTrade" in (perms.get("Spot") or [])
    trade = spot and not read_only
    withdraw = "Withdraw" in (perms.get("Wallet") or [])
    ips = [i for i in result.get("ips") or [] if i and i != "*"]
    expires = None
    expired_at = str(result.get("expiredAt") or "")
    if expired_at and not expired_at.startswith("1970"):
        expires = expired_at[:10]
    extra = "read-only key" if read_only else ""
    return KeyPermissions(trade, withdraw, bool(ips), expires, _summary(trade, withdraw, bool(ips), extra))


def parse_okx(raw: Any) -> KeyPermissions:
    """OKX ``GET /api/v5/account/config`` (the whole answer, ``data[0]`` inside)."""
    data = raw.get("data") if isinstance(raw, dict) else None
    first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
    if first is None or "perm" not in first:
        return KeyPermissions(None, None, None, summary="the answer had no perm field")
    perms = {p.strip() for p in str(first["perm"]).split(",") if p.strip()}
    trade, withdraw = "trade" in perms, "withdraw" in perms
    ip = bool(str(first.get("ip") or "").strip())
    return KeyPermissions(trade, withdraw, ip, None, _summary(trade, withdraw, ip, f"perm={','.join(sorted(perms))}"))


def parse_gate(raw: Any, api_key: str) -> KeyPermissions:
    """Gate ``GET /api/v4/account/main_keys``: every key of the account; ours is found by its key."""
    keys = raw if isinstance(raw, list) else []
    mine = next((k for k in keys if isinstance(k, dict) and k.get("key") == api_key), None)
    if mine is None:
        return KeyPermissions(None, None, None, summary="this key isn't in the account's key list")
    writable = {str(p.get("name", "")).lower() for p in mine.get("perms") or []
                if isinstance(p, dict) and p.get("read_only") is False}
    withdraw: bool | None
    if any("withdraw" in name for name in writable):
        withdraw = True
    elif "wallet" in writable:
        withdraw = None  # a writable wallet area may include withdrawals: not good enough for real money
    else:
        withdraw = False
    state = mine.get("state")
    trade = "spot" in writable and state in (1, None)
    ips = [i for i in mine.get("ip_whitelist") or [] if i]
    extra = "" if state in (1, None) else f"key state {state} (2 = frozen, 3 = locked)"
    if withdraw is None:
        extra = ", ".join(x for x in (extra, "wallet area is read-and-write") if x)
    return KeyPermissions(trade, withdraw, bool(ips), None, _summary(trade, withdraw, bool(ips), extra))


def parse_htx(raw: Any, api_key: str, today: datetime | None = None) -> KeyPermissions:
    """HTX ``GET /v2/user/api-key`` (the whole answer, a list in ``data``); ours is found by its access key."""
    data = raw.get("data") if isinstance(raw, dict) else None
    keys = [k for k in data if isinstance(k, dict)] if isinstance(data, list) else []
    mine = next((k for k in keys if k.get("accessKey") == api_key), None)
    if mine is None or "permission" not in mine:
        return KeyPermissions(None, None, None, summary="this key isn't in HTX's answer")
    perms = {p.strip().lower() for p in str(mine["permission"]).split(",") if p.strip()}
    status = str(mine.get("status") or "normal").lower()
    trade = "trade" in perms and status == "normal"
    withdraw = "withdraw" in perms
    ip = bool(str(mine.get("ipAddresses") or "").strip())
    expires = None
    days = mine.get("validDays")
    if isinstance(days, int) and not isinstance(days, bool) and days >= 0:
        expires = ((today or datetime.now(UTC)) + timedelta(days=days)).strftime("%Y-%m-%d")
    extra = f"permission={','.join(sorted(perms))}" + ("" if status == "normal" else f", key status {status}")
    return KeyPermissions(trade, withdraw, ip, expires, _summary(trade, withdraw, ip, extra))


async def fetch_key_permissions(client: Any, exchange_id: str, api_key: str) -> KeyPermissions | None:
    """Ask the exchange what this key may do (one read-only call). None = no such endpoint here.

    Raises whatever the call raises (network, authentication): the caller decides what that means.
    """
    if exchange_id == "binance":
        return parse_binance(await client.sapi_get_account_apirestrictions())
    if exchange_id == "bybit":
        return parse_bybit(await client.private_get_v5_user_query_api())
    if exchange_id == "okx":
        return parse_okx(await client.private_get_account_config())
    if exchange_id == "gate":
        return parse_gate(await client.private_account_get_main_keys(), api_key)
    if exchange_id == "htx":
        uid = (await client.v2private_get_user_uid()).get("data")
        return parse_htx(await client.v2private_get_user_api_key({"uid": uid, "accessKey": api_key}), api_key)
    return None


def real_money_problem(perms: KeyPermissions | None, exchange: str, require_ip_lock: bool = False) -> str | None:
    """Why this key may not trade real money, or None when it may (trade spot: yes, withdraw: no, and — when
    ``require_ip_lock`` — accepted only from listed IP addresses)."""
    if perms is None:
        return f"{exchange} can't tell us what the API key may do, so it gets no real-money orders"
    if perms.can_withdraw is True:
        return (f"the {exchange} API key can WITHDRAW funds - make a new key with withdrawals OFF "
                f"(read + spot trade only) and put it in .env")
    if perms.can_withdraw is None:
        return (f"couldn't confirm that the {exchange} API key can't withdraw ({perms.summary}) - "
                f"make a key with withdrawals off and nothing but read + spot trade")
    if perms.can_trade_spot is not True:
        return f"the {exchange} API key can't trade spot ({perms.summary}) - enable spot trading on the key"
    if require_ip_lock and perms.ip_restricted is not True:
        how = "isn't locked to an IP address" if perms.ip_restricted is False else "couldn't be confirmed as IP-locked"
        return (f"the {exchange} API key {how} - on {exchange}, restrict the key to this server's fixed IP "
                f"(or set MAINNET_REQUIRE_IP_LOCK=false in .env, which the Phase D plan advises against)")
    return None


def key_warnings(perms: KeyPermissions | None) -> list[str]:
    """Advice that doesn't stop trading: an IP list, a key that expires."""
    if perms is None:
        return []
    warnings = []
    if perms.ip_restricted is False:
        warnings.append("not limited to an IP address (the real-money server requires it: MAINNET_REQUIRE_IP_LOCK)")
    if perms.expires:
        warnings.append(f"the exchange disables this key on {perms.expires}")
    return warnings


def key_note(perms: KeyPermissions | None, problem: str | None) -> str | None:
    """One line for the venue page: the verdict and any advice."""
    if problem:
        return problem
    if perms is None:
        return None
    verdict = "Trade-only key confirmed by the exchange (no withdrawals"
    verdict += ", accepted only from listed IP addresses)" if perms.ip_restricted else ")"
    return "; ".join([verdict, *key_warnings(perms)])
