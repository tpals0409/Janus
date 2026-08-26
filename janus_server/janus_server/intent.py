"""요청 의도 어휘의 단일 원천 (single source of intent lexicons).

runtime(턴 수준 도구 축소)과 adaptive(dispatch 수준 토폴로지)가 같은 낱말을
각자 따로 늘려오던 중복을 여기서 끝낸다. 판정 규칙:

- 영어 낱말은 단어 경계 + 활용형(suffix)으로만 매칭한다. "fixture"가 "fix"로,
  "edition"이 "edit"으로 오검돼 읽기 전용 도구 집합을 잘못 받는 사고를 막는다.
  한글은 조사·활용 결합 때문에 부분 문자열 매칭이 옳다 ("고쳐" ⊂ "고쳐줘").
- 변형(mutating) 신호가 읽기 전용 신호와 공존하면 변형이 이긴다. 필요한 쓰기
  도구를 잘못 지워 작업이 실패하는 편이 여분의 도구를 남겨두는 편보다 비싸다는
  방향 불변식이다 ("조사하고 수정해줘"는 전체 도구로 실행된다).
- 어휘가 하나도 없으면 읽기 전용으로 좁히지 않는다 — 기본값은 전체 도구다.
"""

from __future__ import annotations

import re

# 이 신호만 있고 변형 신호가 없으면, 부모 턴을 read-only 도구 집합으로 축소한다.
READ_ONLY_REQUEST_WORDS = (
    "investigate", "inspect", "research", "analyze", "audit", "explain", "explore",
    "조사", "살펴", "확인", "분석", "검토", "설명", "요약", "파악", "탐색", "훑",
)

# 하나라도 보이면 절대 read-only로 좁히지 않는 변형 신호.
MUTATING_REQUEST_WORDS = (
    "edit", "modify", "write", "implement", "fix", "refactor", "create", "delete",
    "수정", "변경", "작성", "구현", "고쳐", "리팩터", "생성", "삭제", "추가",
)

# adaptive.classify_task의 investigation 토폴로지 신호. 도구 축소와 목적이 다르므로
# 읽기 전용 리스트와 동일시하지 않는다 (diagnose/원인 등 포함, 확인/요약 등 제외).
#
# 읽기 전용 한국어 요청은 "조사"만 쓰지 않는다. 사전에 없는 낱말을 쓰면 general로
# 떨어져 worker fanout이 막힌다.
INVESTIGATION_TASK_WORDS = (
    "investigate", "diagnose", "analyze", "audit", "research", "explain",
    "inspect", "explore",
    "조사", "진단", "분석", "감사", "원인", "파악", "살펴", "탐색", "훑",
)

# analyze→analyzed/analyzing 같은 활용형만 허용하고, -tion 계 명사형(edit+ion=
# edition)은 의도 신호가 아니므로 의도적으로 제외한다.
_ASCII_SUFFIX = r"(?:s|es|ed|ing)?"
_WORD_CACHE: dict[str, re.Pattern[str]] = {}


def _matches(lowered: str, word: str) -> bool:
    """영어는 경계+활용형, 그 외 언어는 부분 문자열로 매칭한다."""
    if not word.isascii():
        return word in lowered
    pattern = _WORD_CACHE.get(word)
    if pattern is None:
        # analyzing/create→creating 처럼 어미 -e 탈락 활용을 흡수한다.
        stem = re.escape(word)
        if word.endswith("e"):
            stem = f"{stem[:-1]}e?"
        pattern = re.compile(rf"\b{stem}{_ASCII_SUFFIX}\b")
        _WORD_CACHE[word] = pattern
    return pattern.search(lowered) is not None


def has_any(text: str | None, words: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(_matches(lowered, word) for word in words)


def has_mutation_intent(text: str | None) -> bool:
    """요청에 하나라도 변형(mutating) 신호가 있는지."""
    return has_any(text, MUTATING_REQUEST_WORDS)


def is_read_only_request(text: str | None) -> bool:
    """읽기 전용 도구 집합으로 좁혀도 되는 요청인지.

    변형 신호가 하나라도 있으면 False다 — 혼합 요청("조사하고 수정해줘")은
    항상 전체 도구로 실행된다.
    """
    return has_any(text, READ_ONLY_REQUEST_WORDS) and not has_mutation_intent(text)


def demo() -> None:
    cases = {
        "Analyze this trace": True,
        "조사해서 요약해줘": True,
        "조사하고 수정해줘": False,
        "analyze the logs and fix the bug": False,
        "audit the test fixtures": True,
        "no keywords here": False,
    }
    for text, expected in cases.items():
        actual = is_read_only_request(text)
        assert actual is expected, f"{text!r}: {actual} != {expected}"
    print("OK — intent 판정 규칙 통과")


if __name__ == "__main__":
    demo()
