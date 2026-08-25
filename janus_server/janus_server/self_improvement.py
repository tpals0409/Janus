"""Conservative, evidence-backed learning from completed Janus work.

This module never changes model weights and never grants tool permissions.  It
turns durable, low-risk evidence into compact context rules that future sessions
can reuse automatically.
"""

from __future__ import annotations

import hashlib
import re

PREFERENCE_CUES = re.compile(
    r"(?:앞으로|항상|매번|먼저|자동으로|하지\s*마|하면\s*안\s*돼|"
    r"from now on|always|never|every time|prefer)", re.IGNORECASE,
)


def fingerprint(kind: str, content: str) -> str:
    normalized = " ".join(content.casefold().split())
    return hashlib.sha256(f"{kind}\0{normalized}".encode()).hexdigest()


def extract_candidates(
    *, task: dict, events: list[dict], verification_runs: list[dict],
) -> list[dict]:
    candidates: list[dict] = []
    seen: set[str] = set()

    for run in verification_runs:
        if run.get("status") != "passed":
            continue
        command = str(run.get("command") or "").strip()
        if not command or command in seen:
            continue
        seen.add(command)
        candidates.append({
            "kind": "verification",
            "title": "검증 명령",
            "content": f"이 프로젝트의 검증에 `{command}` 명령을 사용한다.",
            "confidence": 0.95,
            "evidence": f"task:{task['id']} verification:{run.get('id')}",
        })

    for event in events:
        if event.get("kind") != "transcript":
            continue
        payload = event.get("payload") or {}
        if payload.get("role") != "user":
            continue
        text = str(payload.get("content") or "").strip()
        if not PREFERENCE_CUES.search(text) or not 6 <= len(text) <= 500:
            continue
        candidates.append({
            "kind": "preference",
            "title": "사용자 작업 방식",
            "content": text,
            "confidence": 0.72,
            "evidence": f"task:{task['id']} session:{event.get('session_id')}",
        })

    return candidates
