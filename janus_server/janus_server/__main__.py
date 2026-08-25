"""`python -m janus_server` 진입점.

`-m janus_server.server`는 server 모듈을 __main__과 import 두 이름으로
이중 실행시켜 라우터 순환 import를 깨뜨린다. 반드시 이 경로로 기동한다.
"""

from janus_server.server import main

if __name__ == "__main__":
    main()
