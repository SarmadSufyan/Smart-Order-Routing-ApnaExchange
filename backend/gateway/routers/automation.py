"""Bots & Lab (Phase C): the automation fund, the bots, and the practice lab.

Moving money and starting / resuming / approving need a trader or an admin; anyone
logged in may PAUSE a bot (stopping a bot is always safe). Every action is audited.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.gateway.middleware.auth import User, UserRole, get_current_user, require_role
from backend.services.automation import BotNotFoundError, BotRejectedError, FundError
from backend.shared.models.automation import (
    AdvisorParams,
    BotList,
    BotState,
    CoinsRelease,
    CycleParams,
    FundAmount,
    FundView,
    LabReport,
    LabSample,
    LabSampleKind,
    ProposalDecision,
    SellNowRequest,
)

router = APIRouter(prefix="/api/automation", tags=["automation"])

Trader = Annotated[User, Depends(require_role(UserRole.TRADER, UserRole.ADMIN))]
AnyUser = Annotated[User, Depends(get_current_user)]


def _engine():
    from backend.gateway.dependencies import get_services

    return get_services().automation


async def _call(action, *args):
    try:
        return await action(*args)
    except BotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except (BotRejectedError, FundError) as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc


# ── the automation fund ─────────────────────────────────────────────
@router.get("/fund", response_model=FundView)
async def get_fund(user: AnyUser):
    """The automation fund: what is in it, what each bot holds, and what is left for manual trading."""
    return _engine().fund.view()


@router.post("/fund/deposit", response_model=FundView)
async def deposit(body: FundAmount, user: Trader):
    """Move USDT from the wallet into the automation fund (only what isn't already set aside)."""
    await _call(_engine().fund.deposit, body.amount_usdt, user.username)
    return _engine().fund.view()


@router.post("/fund/withdraw", response_model=FundView)
async def withdraw(body: FundAmount, user: Trader):
    """Move unassigned USDT from the fund back to the wallet (a bot's money comes back when it is stopped)."""
    await _call(_engine().fund.withdraw, body.amount_usdt, user.username)
    return _engine().fund.view()


# ── bots ────────────────────────────────────────────────────────────
@router.get("/bots", response_model=BotList)
async def list_bots(user: AnyUser):
    return BotList(bots=_engine().list())


@router.post("/bots/cycle", response_model=BotState, status_code=201)
async def start_cycle(body: CycleParams, user: Trader):
    """Start a profit-cycle bot: buy a small lot, sell it only above cost + both fees + the threshold, repeat.

    Its budget comes out of the automation fund's unassigned money.
    """
    return await _call(_engine().create_cycle, body, user.username)


@router.post("/bots/advisor", response_model=BotState, status_code=201)
async def start_advisor(body: AdvisorParams, user: Trader):
    """Start an advisor: it proposes bracketed buys with its reasons. ``mode=AUTO`` (approves itself within its
    limits) is testnet only."""
    return await _call(_engine().create_advisor, body, user.username)


@router.get("/bots/{bot_id}", response_model=BotState)
async def get_bot(bot_id: str, user: AnyUser):
    try:
        return _engine().view(_engine().get(bot_id))
    except BotNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


@router.post("/bots/{bot_id}/pause", response_model=BotState)
async def pause_bot(bot_id: str, user: AnyUser):
    return await _call(_engine().pause, bot_id, user.username)


@router.post("/bots/{bot_id}/resume", response_model=BotState)
async def resume_bot(bot_id: str, user: Trader):
    return await _call(_engine().resume, bot_id, user.username)


@router.post("/bots/{bot_id}/stop", response_model=BotState)
async def stop_bot(bot_id: str, user: Trader):
    """No new trades; the bot's cash goes back to the fund. Open lots stay until sold or released."""
    return await _call(_engine().stop_bot, bot_id, user.username)


@router.post("/bots/{bot_id}/sell-now", response_model=BotState)
async def sell_now(bot_id: str, body: SellNowRequest, user: Trader):
    """Sell open lots at the market now — even below what they cost. The owner's decision, audited."""
    return await _call(_engine().sell_now, bot_id, body.lot_id, user.username)


@router.post("/bots/{bot_id}/release-coins", response_model=BotState)
async def release_coins(bot_id: str, body: CoinsRelease, user: Trader):
    """A stopped bot's open lots leave its book without a sale: to manual trading, or written off as lost."""
    return await _call(_engine().release_coins, bot_id, body.how, user.username)


@router.post("/proposals/{proposal_id}/decide", response_model=BotState)
async def decide(proposal_id: str, body: ProposalDecision, user: Trader):
    """Approve (buy now, with the proposal's stop and target) or reject an advisor proposal."""
    return await _call(_engine().decide, proposal_id, body.approve, user.username)


# ── the practice lab ────────────────────────────────────────────────
@router.get("/lab", response_model=LabReport)
async def lab_report(user: AnyUser):
    """What our own bots' trades have taught us: costs, variant scoreboard, comparisons, outcome models."""
    engine = _engine()
    return await engine.lab.report(engine.bots())


@router.get("/lab/samples", response_model=list[LabSample])
async def lab_samples(user: AnyUser, kind: LabSampleKind | None = None,
                      limit: Annotated[int, Query(ge=1, le=2_000)] = 200):
    samples = sorted(_engine().lab.samples(kind), key=lambda s: s.opened_at, reverse=True)
    return samples[:limit]


@router.post("/lab/presets/{name}", response_model=BotList, status_code=201)
async def start_preset(name: str, user: Trader):
    """Start a lab experiment (testnet only): moves its budget from the wallet into the fund and starts its bots."""
    return BotList(bots=await _call(_engine().start_preset, name, user.username))
