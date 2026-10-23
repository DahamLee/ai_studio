"""편집기가 부르는 검증 진입점. 그래프 JSON을 표준입력으로 받아 결과 JSON을 표준출력에 쓴다.

실행: echo '{"nodes": [...], "edges": [...]}' | python -m ai_studio.n17_editor_api
웹 개발 서버(vite.config.ts)가 /api/validate 요청마다 이 모듈을 부른다.
"""

import json
import sys

from ai_studio.n13_connect import check_editor_graph


def main() -> None:
    print(json.dumps(check_editor_graph(json.load(sys.stdin)), ensure_ascii=False))


if __name__ == "__main__":
    main()
