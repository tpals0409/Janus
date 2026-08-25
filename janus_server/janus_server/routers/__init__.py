"""Janus 서버 도메인 라우터 묶음 — server.py가 하단에서 include한다."""

# 라우터를 먼저 import하면 server가 부분 초기화 상태로 라우터를 붙이다 죽는다.
# server를 여기서 먼저 완성시켜 어느 방향의 import도 안전하게 만든다.
from .. import server  # noqa: F401
