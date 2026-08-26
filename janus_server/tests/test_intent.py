"""intent 모듈 — 의도 어휘 단일 원천과 판정 규칙 계약."""

from __future__ import annotations

import pytest

from janus_server import adaptive as adaptive_mod
from janus_server import intent as intent_mod
from janus_server import runtime


@pytest.mark.parametrize("text", [
    "Analyze this trace",
    "please inspect the module",
    "조사해서 요약해줘",
    "코드 구조를 살펴봐 줘",
])
def test_pure_read_only_requests(text: str):
    assert intent_mod.is_read_only_request(text) is True


@pytest.mark.parametrize("text", [
    "조사하고 수정해줘",
    "analyze the logs and fix the bug",
    "구현 계획을 검토 후 작성해줘",  # 검토(읽기) + 작성(변형)
])
def test_mutation_intent_dominates_mixed_requests(text: str):
    # 방향 불변식: 혼합 요청은 절대 read-only로 좁히지 않는다.
    assert intent_mod.is_read_only_request(text) is False


def test_unknown_language_defaults_to_full_tools():
    assert intent_mod.is_read_only_request("이거 왜 이런 거야?") is False
    assert intent_mod.is_read_only_request("") is False
    assert intent_mod.is_read_only_request(None) is False


def test_ascii_word_boundaries_stop_substring_false_positives():
    # 과거에는 부분 문자열 매칭이라 fix ⊂ fixtures / edit ⊂ edition 이 변형 신호로
    # 오검돼 읽기 전용 축소가 풀렸다. 경계 매칭으로 순수 조사 요청을 되살린다.
    assert intent_mod.is_read_only_request("audit the test fixtures") is True
    assert intent_mod.is_read_only_request("explain edition differences") is True
    # 반대로 실제 변형형(fixed/edited)은 여전히 잡는다.
    assert intent_mod.has_mutation_intent("already fixed the loader") is True
    assert intent_mod.is_read_only_request(
        "it is fixed now; explain why it broke") is False


def test_ascii_suffix_forms_stay_matched():
    assert intent_mod.is_read_only_request("analyzing the results") is True
    assert intent_mod.is_read_only_request("researched prior approaches") is True
    assert intent_mod.has_mutation_intent("editing the config") is True


def test_korean_keeps_substring_matching_for_agglutination():
    assert intent_mod.has_mutation_intent("고쳐줘") is True
    assert intent_mod.is_read_only_request("살펴줘") is True
    # 보수 편향: 수정사항을 '요약'하는 요청도 변형 우선 규칙으로 전체 도구를 유지한다.
    assert intent_mod.is_read_only_request("수정사항을 요약해줘") is False


def test_runtime_wrapper_delegates_to_intent_module():
    assert runtime.is_read_only_request("분석해줘") is True
    assert runtime.is_read_only_request("수정해줘") is False


def test_adaptive_reuses_the_shared_investigation_lexicon():
    task = {"title": "코드베이스를 조사하고 구조를 파악해줘"}
    task_class, signals = adaptive_mod.classify_task(task)
    assert task_class == "investigation"
    assert signals == ["investigation_language"]

    en_task = {"title": "diagnose the flaky websocket test"}
    assert adaptive_mod.classify_task(en_task)[0] == "investigation"

    # 우선순위는 이동 없이 그대로 — 계획 언어가 조사 언어보다 먼저다.
    planned = {"objective": "조사 결과를 바탕으로 implementation plan 작성"}
    assert adaptive_mod.classify_task(planned)[0] == "planning"


def test_module_demo_self_check():
    intent_mod.demo()
