from datetime import datetime

from pydantic import BaseModel


class ConsensusNode(BaseModel):
    id: int
    address: str
    reachable: bool
    role: str | None = None  # FOLLOWER / CANDIDATE / LEADER
    term: int | None = None
    leader: int | None = None  # who this node thinks leads
    commit_index: int | None = None
    applied_index: int | None = None
    pid: int | None = None
    mode: str | None = None  # testnet / mainnet: must match the gateway's
    rejected_messages: int | None = None  # forged / too old / replayed lines this node refused


class ConsensusStatus(BaseModel):
    enabled: bool
    signed: bool = False  # every RAFT message carries an HMAC-SHA256 signature
    leader: int | None = None
    majority_reachable: bool = False
    nodes: list[ConsensusNode] = []
    pending_writes: list[str] = []  # our changes the cluster hasn't confirmed yet
    synced_index: int = 0  # committed log index this gateway has adopted
    last_sync: datetime | None = None
    message: str = ""
