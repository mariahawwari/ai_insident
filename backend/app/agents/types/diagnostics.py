from typing import TypedDict, Optional

class Check(TypedDict):
    name: str
    ok: bool
    detail: Optional[str]
