from typing import Annotated

from fastapi import APIRouter, Depends

from backend.gateway.middleware.auth import User, get_current_user
from backend.shared.models.consensus import ConsensusStatus

router = APIRouter(prefix="/api/consensus", tags=["consensus"])


@router.get("/status", response_model=ConsensusStatus)
async def consensus_status(user: Annotated[User, Depends(get_current_user)]):
    """The RAFT cluster as this gateway last saw it (re-read every ~0.5 s):
    each node's role, term and log position, the leader, and any of our
    changes the cluster hasn't confirmed yet."""
    from backend.gateway.dependencies import get_services

    return get_services().risk_controls.status()
