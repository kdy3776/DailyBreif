#!/usr/bin/env python3
"""
향후 3개월 / 6개월 시장 전망(시나리오) 생성기
------------------------------------------------------------
데일리 브리핑과 같은 Claude + 웹서치 설정을 재사용해, 더 긴 호흡의
포워드 시나리오 분석을 만듭니다. 웹앱의 '전망' 탭과 월간 스케줄러가 사용.

※ 시나리오·확률은 가정에 기반한 추정이며 투자 권유·수익 보장이 아닙니다.
"""

import datetime
from zoneinfo import ZoneInfo

import anthropic

from daily_briefing import (
    MODEL, WEB_SEARCH_TOOL, MAX_SEARCHES, MAX_TOKENS, TIMEZONE, _extract_sources,
)

OUTLOOK_SYSTEM = """\
당신은 한국어로 '향후 3개월·6개월' 시장 전망을 쓰는 전략 애널리스트입니다.
독자는 한국·미국 주식 투자자입니다. 데일리 시세가 아니라 '중기 흐름과 시나리오'에 초점.

[원칙]
- 당신은 투자자문가가 아닙니다. 단정적 예측·수익 보장 금지. '시나리오'와 '조건부 전망'으로 제시.
- 확률은 정량 단정이 아니라 상대적 가능성(높음/중간/낮음)으로 표현하고 근거를 답니다.
- 모든 전제·지표는 웹서치로 확인한 최신 사실에 기반. 기억에 의존 금지.
- 시나리오는 항상 '무엇이 맞으면 / 무엇이 틀리면(트리거·반증 지표)'을 같이 제시.

[웹서치] 다음을 확인: 통화정책 경로(연준·ECB·한은), 인플레·고용 추세, 기업이익 전망,
주요 지정학(진행 중 분쟁·선거·정책), AI·반도체 등 구조적 테마, 원자재·환율.

[출력 형식] 마크다운으로 아래 구조를 따르세요.
# 🔮 향후 전망 — {DATE} 기준

## 한눈에
- (3개월·6개월 큰 그림 3~4줄)

## 🌍 거시 환경 (베이스 시나리오)
- 금리·인플레·성장·환율의 향후 경로 가정과 근거.

## 📈 시나리오 (3개월 & 6개월)
각 구간에 대해 표로:
| 시나리오 | 가능성 | 핵심 가정 | 시장 함의 | 반증 트리거 |
(Bull / Base / Bear 3종, 3개월·6개월 각각)

## 🏭 섹터 로드맵
- 향후 3~6개월 유망/주의 섹터와 이유(한·미). 구조적 vs 경기민감 구분.

## ⚠️ 핵심 리스크 & 모니터링 지표
- 앞으로 꼭 지켜봐야 할 이벤트·지표 리스트(날짜 있으면 명시).

## 🧭 전략 시사점
- 중기 관점의 포지셔닝 아이디어(자산배분 톤). 단정적 매수·매도 아님.

---
*본 전망은 정보 제공 목적이며 투자 권유가 아닙니다. 시나리오·확률은 가정 기반 추정으로 실제 결과를 보장하지 않습니다.*
"""


def generate_outlook() -> str:
    """3개월·6개월 전망 마크다운 생성(출처 포함)."""
    client = anthropic.Anthropic()
    now = datetime.datetime.now(ZoneInfo(TIMEZONE))
    system = OUTLOOK_SYSTEM.replace("{DATE}", now.strftime("%Y년 %m월 %d일"))
    user = (
        f"지금은 {now.strftime('%Y년 %m월 %d일')} 기준입니다. "
        f"향후 3개월과 6개월 시장 전망을 작성하세요. 반드시 웹서치로 최신 거시·정책·이익 전망을 "
        f"확인한 뒤, 시스템 지침의 형식과 시나리오 구조를 지키세요."
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[{
            "type": WEB_SEARCH_TOOL,
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
            "user_location": {"type": "approximate", "country": "KR", "timezone": TIMEZONE},
        }],
    )

    parts = [b.text for b in resp.content if b.type == "text"]
    md = "\n".join(p for p in parts if p).strip()
    if not md:
        raise RuntimeError("전망 본문이 비어 있습니다.")

    sources = _extract_sources(resp)
    if sources:
        lines = [f"{i}. [{t}]({u})" for i, (t, u) in enumerate(sources, 1)]
        md += "\n\n## 📎 출처\n" + "\n".join(lines)
    return md


if __name__ == "__main__":
    print(generate_outlook())
