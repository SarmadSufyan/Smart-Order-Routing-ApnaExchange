from typing import Annotated

from fastapi import APIRouter, Depends

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.copilot import CopilotRequest, CopilotStatus, Proposal, ProposalAction

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


def _copilot():
    from backend.gateway.dependencies import get_services

    return get_services().copilot


@router.get("/status", response_model=CopilotStatus)
async def copilot_status(user: Annotated[User, Depends(get_current_user)]):
    """Whether the copilot can be used (an LLM key is configured), and which model."""
    return _copilot().status()


@router.post("/propose", response_model=Proposal)
async def propose(body: CopilotRequest, user: Annotated[User, Depends(get_current_user)]):
    """Turn one sentence into a proposal: PROPOSED, CLARIFY (a question) or REFUSED.

    Nothing is sent to any exchange. A PROPOSED proposal expires unless confirmed.
    """
    return await _copilot().propose(body.text, user.username)


@router.post("/confirm", response_model=Proposal)
async def confirm(body: ProposalAction, user: Annotated[User, Depends(get_current_user)]):
    """Submit your own PROPOSED proposal through the normal order path.

    The price is checked again first; then the order (or TWAP) passes the same
    pre-trade risk, kill switch and funding gates as the order button.
    """
    return await _copilot().confirm(body.proposal_id, user.username)


@router.post("/cancel", response_model=Proposal)
async def cancel(body: ProposalAction, user: Annotated[User, Depends(get_current_user)]):
    return await _copilot().cancel(body.proposal_id, user.username)
