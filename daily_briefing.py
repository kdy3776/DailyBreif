#!/usr/bin/env python3
"""
데일리 투자 브리핑 자동 생성 & 이메일 발송 스크립트
------------------------------------------------------------
매일 정해진 시간에 실행되면:
  1) Claude API(웹서치 툴)로 미국·한국 시장, 거시·지정학, 산업 구조,
     다음 주 일정, 그리고 뉴스 + Stocktwits 트렌딩 기반 주목 종목을 조사·작성
  2) 결과를 HTML 이메일로 변환해 지정한 주소로 발송

GitHub Actions(또는 cron)에 올려서 무인으로 굴리는 것을 전제로 합니다.
필요한 환경변수는 README.md 참고.

※ 본 스크립트가 만들어내는 내용은 투자 정보 정리이며, 투자 권유나 자문이 아닙니다.
"""

import os
import sys
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from zoneinfo import ZoneInfo

import anthropic
import markdown as md

from macro_charts import build_macro_dashboard


# ──────────────────────────────────────────────────────────────
# 설정 (환경변수로 덮어쓸 수 있음)
# ──────────────────────────────────────────────────────────────
MODEL = os.environ.get("BRIEFING_MODEL", "claude-sonnet-4-6")
# 웹서치 툴 버전: 기본은 어디서나 되는 20250305.
# 동적 필터링이 되는 최신판을 쓰려면 환경변수로 web_search_20260209 지정.
WEB_SEARCH_TOOL = os.environ.get("WEB_SEARCH_TOOL", "web_search_20250305")
MAX_SEARCHES = int(os.environ.get("BRIEFING_MAX_SEARCHES", "12"))
MAX_TOKENS = int(os.environ.get("BRIEFING_MAX_TOKENS", "15000"))
TIMEZONE = os.environ.get("BRIEFING_TZ", "Asia/Seoul")

# 그날 상세분석할 종목 개수 (기본: 5개)
STOCK_PICKS = os.environ.get("BRIEFING_STOCK_PICKS", "5")


SYSTEM_PROMPT = """\
당신은 한국어로 매일 아침 투자 브리핑을 작성하는 애널리스트입니다.
독자는 한국·미국 주식에 투자하며 자기계발 목적으로도 시장을 공부하는 개인입니다.

[중요 원칙]
- 당신은 투자자문가가 아닙니다. 단정적인 "사라/팔아라"나 수익 보장은 절대 하지 않습니다.
  종목은 '상승 논리(강세)'와 '리스크(약세)'를 함께 제시하고, 최종 판단은 독자에게 맡깁니다.
- 트렌딩 = 좋은 종목이 아니라 '시끄러운 종목'입니다. 언급량이 많아도 실제 촉매
  (실적·계약·규제·신제품 등)가 없으면 "펀더멘털보다 분위기(과열/밈성)"라고 명확히 표시하세요.
- 모든 수치·전망은 출처가 분명한 것만 쓰고, 애널리스트 추정치는 "○○증권 추정"처럼 귀속을 밝힙니다.
- 확실하지 않으면 단정하지 말고 불확실성을 그대로 적습니다.
- **일관성**: 리포트는 하나의 스토리여야 합니다. 3분 요약·증시·거시 지표·핵심 이슈에서 짚은
  내용이 섹터 워치리스트와 주목 종목 선정 사유로 자연스럽게 이어지게 하세요. 워치리스트·주목 종목의
  사유는 반드시 '그날의 거시·증시·이슈 맥락'과 연결돼 "왜 지금 이게 후보인지"가 드러나야 합니다.

[웹서치 지침]
다음을 검색해 최신 정보로 채우세요:
  · 미국 증시 직전 마감(주요 지수, 큰 변동 종목)
  · 코스피/코스닥 직전 마감 및 수급·쏠림 이슈
  · 거시·지정학(금리·물가·환율, 진행 중인 주요 분쟁/정책)
  · AI·반도체 등 산업 구조 변화
  · 다음 거래일/다음 주 핵심 일정(고용지표, FOMC 등)
  · 'Stocktwits trending tickers' 및 뉴스에서 언급량 많은 종목

[최신성 원칙 — 중요]
- 모든 시세·지표·뉴스는 '지금' 시점으로 새로 검색해 확인하세요. 기억(훈련 데이터)에 의존한
  수치는 절대 쓰지 마세요. 가물가물하면 반드시 검색으로 재확인.
- 목표는 **발송 시점 기준 최대 1시간 이내의 최신 정보**입니다. 검색어에 오늘 날짜·'today'·'latest'를
  넣고, 가능한 한 가장 최근(1시간 내 → 안 되면 당일) 출처를 쓰세요. 며칠 지난 기사로 핵심 수치를 채우지 마세요.
- **각 핵심 데이터에 '기준 시점'을 반드시 표기**하세요. 예: "코스피 8,476 (오늘 14:30 장중)",
  "S&P 7,580 (5/29 마감)", "나스닥 선물 +0.4% (오늘 08:10 ET 시외)", "WTI $91 (오늘 09:00)".

[시장 상태별 처리 — 미국/한국 각각 따로 판단]
프롬프트에 주어진 ET·KST 시각으로 각 시장이 '장전 / 장중 / 장후' 중 무엇인지 판단하세요.
(미국 정규장 ET 09:30~16:00, 한국 정규장 KST 09:00~15:30 / 주말·공휴일 휴장)
- **장전(개장 전):** 직전 거래일 '종가'를 기준으로 쓰되, 반드시 **시외(프리마켓/애프터마켓·선물·시간외)**
  움직임을 같이 확인해 "전일 종가 + 현재 시외/선물 방향"으로 적으세요. (예: "전일 종가 X, 현재 프리마켓 +0.5%")
- **장중:** 직전 종가가 아니라 **장중 현재가·당일 등락률**을 최대한 반영하세요. "(오늘 HH:MM 장중)"으로 표기.
- **장후(마감 후):** 당일 '종가'를 기준으로 쓰고, 시간외 움직임이 크면 함께 표기.
- 미국과 한국의 상태가 다를 수 있으니(예: 한국 장중·미국 장전) 각 시장을 위 규칙으로 따로 처리하세요.
- 발송 시점 기준 이미 끝난 일정은 '다음 일정'에서 빼고, 아직 안 온 일정만 넣으세요.

[출력 형식] — 아래 구조를 마크다운으로 그대로 따르세요.
# 📅 데일리 투자 브리핑 — {DATE}

## ⚡ 3분 요약
- (시장 한 줄 요약)
- (핵심 포인트 4~5개, 미국·한국 섞어서)

## 📊 더보기
**🇺🇸 미국 증시** — (2~4문장)
**🇰🇷 한국 증시** — (2~4문장)
**🌍 거시·지정학** — (2~4문장)
**🏭 산업 구조** — (2~4문장)
**📆 다음 일정 관전 포인트** — 아래처럼 마크다운 '표(캘린더)'로 작성.
각 거래일을 한 행으로, 컬럼은 [요일/날짜 | 경제지표 | 실적]. 가장 중요한 날에는 🎯 표시.
표 아래에 "그 다음 →" 한 줄로 그 이후 핵심 일정(예: FOMC), 마지막에 ">" 인용구로 한 주 흐름 요약.

| 요일 | 경제지표 | 실적 |
|---|---|---|
| 월 M/D | ... | ... |
| ... | ... | ... |

## 🏦 거시 지표 대시보드
먼저 그날 거시 흐름을 1~2문장으로 코멘트하세요(웹서치로 확인한 핵심 지표 방향 위주).
그 다음 아래 두 토큰을 각각 단독 줄로 출력하세요. 시스템이 자동으로:
  · {{MACRO_TABLE}} → 현재값 / 3·6·12개월 평균 표(FRED 실데이터)로 치환
  · {{MACRO_CHART}} → 12개월 추이 그래프 이미지로 치환
표·그래프 수치는 직접 쓰지 말고 토큰만 두세요(중복 방지). 단, 코멘트에는 핵심 수치 한두 개를 녹여도 됩니다.

{{MACRO_TABLE}}

{{MACRO_CHART}}

## 🏭 산업 섹터 분석
아래 11개 섹터를 '대구분'으로 매일 다루세요. 그날 이슈가 큰 섹터를 위로 정렬하고, 표로 작성:
[섹터 | 모멘텀 | 🔵저평가 | 🟠과매도 | 🔥트렌딩].
- 모멘텀: 🔥과열·▲강세·→중립·▼약세 중 하나.
- 각 섹터마다 관심종목을 3개 선정 — 서로 다른 기준으로 하나씩:
  · 🔵저평가: 밸류 기준 1종 (낮은 PER/PBR, 업종 평균 이하 등)
  · 🟠과매도: 낙폭과대 기준 1종 (52주 최저권, RSI 과매도, 고점 대비 큰 조정 등)
  · 🔥트렌딩: Stocktwits 언급량 상위(트렌딩) 기준 1종
- 각 칸은 "티커/이름(선정 사유 한 줄)" 형식. 사유는 ① 그 기준(밸류/낙폭/트렌딩)의 근거에
  ② '왜 지금 이게 워치리스트인지'를 위의 3분 요약·증시·거시·핵심 이슈와 연결해 적으세요.
  단순 지표만 쓰지 말 것. (예: 🔵 "S-Oil(유가 급등 국면 수혜인데 선행PER 6배)",
  🟠 "삼성전자(HBM 슈퍼사이클인데 하이닉스 대비 -30% 소외)", 🔥 "코인베이스(BTC 급등에 언급량 1위)")
- 한·미 가리지 말고 선정. 단정적 매수 추천이 아니라 각 기준으로 '해석될 수 있는' 후보임을 전제로 함.

섹터 11개(대표 종목은 참고용 예시):
- 반도체 — SK하이닉스·삼성전자 / 엔비디아·브로드컴·마이크론
- 보안 — 안랩·윈스 / 크라우드스트라이크·팔로알토·지스케일러
- 전통제조 — 현대차·POSCO·HD현대중공업 / 캐터필러·GE·디어
- 드론 — 퍼스텍·베셀·제이씨현 / 크라토스·에어로바이런먼트
- 크립토 — 우리기술투자·두나무 관련 / 코인베이스·스트래티지(MSTR)·마라톤
- 에너지 — S-Oil·SK이노베이션 / 엑손모빌·셰브론
- 전력 — 한국전력·두산에너빌리티·LS일렉트릭 / GE Vernova·비스트라·콘스텔레이션
- 은행 — KB금융·신한지주 / JP모건·뱅크오브아메리카
- 핀테크 — 카카오페이·네이버페이 / 로빈후드·페이팔·블록
- 바이오 — 셀트리온·삼성바이오로직스·유한양행 / 일라이릴리·노보노디스크·암젠
- 식품 — 오리온·CJ제일제당·농심 / 크래프트하인즈·몬델리즈·타이슨푸드

| 섹터 | 모멘텀 | 🔵저평가 | 🟠과매도 | 🔥트렌딩 |
|---|---|---|---|---|
| ... | ... | ...(사유) | ...(사유) | ...(사유) |

표 바로 아래에 **"핵심 이슈 & 근거"** 소제목으로, 각 섹터의 핵심 이슈(2~3개)와 그 근거
(어떤 발표·실적·지표·뉴스인지)를 섹터별 1줄씩 요약하세요. 모든 내용은 웹서치로 확인한 사실 기반.
그 다음 ">" 인용구로 '오늘 가장 주목할 섹터 한 줄'을 덧붙이세요.

※ 위 표의 33개(11섹터 × 3) 종목은 '사유가 달린 워치리스트'일 뿐, 심층 분석이 아닙니다.
아래 '오늘의 주목 종목'에서 이 33개 중 추정 기대수익률이 높은 {PICKS}개를 골라 깊게 분석합니다.

## 🔬 오늘의 주목 종목 ({PICKS}개)
선정 규칙: 위 섹터 표의 33개 관심종목을 모수로, '추정 기대수익률'이 가장 높다고 판단되는
{PICKS}개를 골라 심층 분석하고, 기대수익률 높은 순으로 정렬. (저평가·과매도·트렌딩 유형이 고루 섞이면 좋음.)
※ 모든 기대수익률은 가정에 기반한 추정치이며 보장이 아님.
### [티커] 회사명 (🇺🇸 또는 🇰🇷) — [선정유형: 저평가/과매도/트렌딩]
- **사업모델** — 다음 셋으로 분해:
  · *매출원*: 어디서 돈을 버는지(주요 사업부·제품과 대략적 비중)
  · *비용구조*: 돈이 어디로 나가는지(설비투자·R&D·원재료·인건비 등 핵심 항목)
  · *핵심 고객*: 누구에게 파는지(주요 고객사/고객군, 매출 집중도)
- **촉매(왜 지금):** ...
- **실적·밸류 스냅샷:** ...
- **추정 기대수익률(추정·비보장):** 예 "+15~20% 내외" + 산출 근거(목표가 대비 괴리·밸류 정상화·촉매 실현 등). 가정 기반 추정임을 명시.
- **상승 논리(강세):** ...
- **리스크(약세):** ...
- **체크포인트:** ...
- **밸류/과열 진단:** 저평가 / 과매도 / 트렌딩(분위기) 중 명확히 표시(+근거)

## 🎓 오늘의 산업 딥다이브
매일 '하나의 산업/밸류체인/기술'을 골라 깊이 있게 해부하는 미니 레슨 코너입니다.
주식시장·투자 개념이 아니라 **산업 그 자체의 구조**를 이해하는 데 초점을 둡니다.
(예: HBM 공급망, 파운드리 vs 팹리스, 정유 크랙스프레드 구조, AI 데이터센터 전력 밸류체인,
 GLP-1 비만치료제 밸류체인, 방산 드론 조달 구조, 스테이블코인 인프라, 결제망 작동 방식,
 조선 수주 사이클, 2차전지 소재 체인 등)
- 가능하면 그날 브리핑에서 부각된 섹터와 연결되는 산업을 고르되, 독립 주제도 좋음.
- 매일 다른 주제로(최근 다룬 것 반복 금지). 맨 위에 '### 오늘의 산업: ○○' 제목을 달고,
  아래 구조로 충분히 상세하게(전체 8~12문장 이상) 풀어 쓰세요:
  1) **개요** — 이 산업/밸류체인이 무엇이고 무엇을 만드는지
  2) **밸류체인** — 업스트림→미드스트림→다운스트림, 단계별로 누가 무슨 역할을 하는지
  3) **수익 구조** — 어디서 돈이 나고, 마진을 결정하는 요인(가격·원가·사이클)
  4) **핵심 플레이어** — 단계별 주요 기업(한·미)과 각자의 위치·점유
  5) **구조적 역학** — 진입장벽·해자, 사이클·규제·기술 변화 등 산업을 움직이는 힘
  6) **지금의 포인트** — 현재 이 산업에서 벌어지는 변화/논쟁 한 가지
- 전문용어는 풀어서, 초보도 이해되게. 단 깊이는 충분히.

---
*본 브리핑은 정보 제공 목적이며 투자 권유가 아닙니다. '추정 기대수익률'은 가정에 기반한 추정치로 실제 수익을 보장하지 않으며, 투자 판단과 책임은 본인에게 있습니다.*
"""


def build_user_prompt() -> str:
    now = datetime.datetime.now(ZoneInfo(TIMEZONE))
    date_str = now.strftime("%Y년 %m월 %d일 (%a) %H:%M")
    tzname = now.tzname() or TIMEZONE
    # 시장 상태 판단을 돕도록 미국 동부·한국 시각도 함께 제공
    et = now.astimezone(ZoneInfo("America/New_York"))
    kst = now.astimezone(ZoneInfo("Asia/Seoul"))
    et_str = et.strftime("%m/%d %H:%M (%a)")
    kst_str = kst.strftime("%m/%d %H:%M (%a)")
    return (
        f"지금은 {date_str} ({tzname}) 기준입니다. "
        f"참고로 같은 순간이 미국 동부시간(ET)으로는 {et_str}, 한국시간(KST)으로는 {kst_str}입니다. "
        f"이 시점 기준의 데일리 투자 브리핑을 작성하세요. "
        f"주목 종목은 {STOCK_PICKS}개 범위에서 그날 이슈에 따라 정하세요. "
        f"모든 시세·지표·뉴스는 반드시 '지금' 시점으로 새로 웹서치해 확인하고(기억에 의존 금지), "
        f"각 핵심 수치에 기준 시점을 표기하세요. 시스템 지침의 형식과 '시장 상태별 처리' 규칙을 지키세요."
    )


def generate_briefing() -> str:
    """Claude API + 웹서치로 브리핑 본문(마크다운)을 생성."""
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용

    now = datetime.datetime.now(ZoneInfo(TIMEZONE))
    system = SYSTEM_PROMPT.replace("{DATE}", now.strftime("%Y년 %m월 %d일")) \
                          .replace("{PICKS}", STOCK_PICKS)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": build_user_prompt()}],
        tools=[{
            "type": WEB_SEARCH_TOOL,
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
            "user_location": {
                "type": "approximate",
                "country": "KR",
                "timezone": TIMEZONE,
            },
        }],
    )

    # 웹서치 응답은 text / server_tool_use / web_search_tool_result 블록이 섞여 있음.
    # 우리가 메일로 보낼 건 text 블록만 이어 붙인 것.
    parts = [block.text for block in response.content if block.type == "text"]
    briefing = "\n".join(p for p in parts if p).strip()
    if not briefing:
        raise RuntimeError("브리핑 본문이 비어 있습니다. 모델 응답을 확인하세요.")

    sources = _extract_sources(response)
    if sources:
        lines = [f"{i}. [{title}]({url})" for i, (title, url) in enumerate(sources, 1)]
        briefing += "\n\n## 📎 출처 (이 브리핑이 참고한 웹 소스)\n" + "\n".join(lines)
    return briefing


def _extract_sources(response, limit=15):
    """웹서치 인용/결과 블록에서 (제목, URL)을 수집해 중복 제거."""
    seen, out = set(), []

    def add(title, url):
        if url and url not in seen:
            seen.add(url)
            out.append(((title or url).strip(), url.strip()))

    for block in response.content:
        btype = getattr(block, "type", "")
        # 1) 본문에서 실제 인용된 출처 (가장 관련성 높음)
        if btype == "text":
            for c in (getattr(block, "citations", None) or []):
                add(getattr(c, "title", None), getattr(c, "url", None))
        # 2) 검색이 찾은 결과 페이지들
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None) or []
            for item in content:
                add(getattr(item, "title", None), getattr(item, "url", None))

    return out[:limit]


def _macro_table_md(rows):
    """FRED 평균 데이터를 마크다운 표로. rows=[(label,현재,3M,6M,12M)]"""
    if not rows:
        return ""
    def fmt(v):
        if v is None:
            return "-"
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}"
    out = ["| 지표 | 현재 | 3M 평균 | 6M 평균 | 12M 평균 |",
           "|---|---|---|---|---|"]
    for label, cur, a3, a6, a12 in rows:
        out.append(f"| {label} | {fmt(cur)} | {fmt(a3)} | {fmt(a6)} | {fmt(a12)} |")
    return "\n".join(out)


def send_email(markdown_body: str, chart_path: str | None = None, macro_rows=None) -> None:
    """마크다운을 HTML로 변환해 이메일 발송. chart_path=인라인 그래프, macro_rows=평균표 데이터."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    # 실수로 scheme이나 포트가 붙은 경우 정리 (예: "https://smtp.gmail.com:465")
    smtp_host = smtp_host.replace("https://", "").replace("http://", "").strip("/ ")
    if ":" in smtp_host:
        smtp_host = smtp_host.split(":")[0].strip()
    if not smtp_host:
        smtp_host = "smtp.gmail.com"
    smtp_port = int(os.environ.get("SMTP_PORT", "465").strip() or "465")
    smtp_user = os.environ["SMTP_USER"].strip()
    smtp_password = os.environ["SMTP_PASSWORD"]  # 비번은 strip 안 함(공백이 의미 있을 수 있음)
    email_from = os.environ.get("EMAIL_FROM", smtp_user).strip()
    email_to = [a.strip() for a in os.environ["EMAIL_TO"].split(",") if a.strip()]

    now = datetime.datetime.now(ZoneInfo(TIMEZONE))
    subject = f"📈 데일리 투자 브리핑 — {now.strftime('%Y-%m-%d')}"

    # 거시 평균 표 토큰 치환 (마크다운 변환 전)
    table_md = _macro_table_md(macro_rows)
    markdown_body = markdown_body.replace(
        "{{MACRO_TABLE}}", table_md or "*(FRED 키 미설정 시 평균 표 생략)*")

    # 플레인텍스트는 차트 토큰 제거
    plain_body = markdown_body.replace("{{MACRO_CHART}}", "[거시 12개월 추이 그래프 — HTML 메일에서 확인]")

    html_body = md.markdown(markdown_body, extensions=["extra", "sane_lists", "tables"])
    # 차트 토큰을 실제 이미지(cid) 또는 빈 문자열로 치환 (md가 <p>로 감쌀 수 있어 양쪽 처리)
    if chart_path:
        img_tag = ('<img class="macro" src="cid:macro_chart" alt="Macro Dashboard">')
    else:
        img_tag = ""
    html_body = html_body.replace("<p>{{MACRO_CHART}}</p>", img_tag).replace("{{MACRO_CHART}}", img_tag)

    gen_time = now.strftime("%Y-%m-%d %H:%M KST")
    html = f"""\
<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:24px 12px; background:#eef1f6;
         font-family:-apple-system,'Segoe UI',Roboto,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
         color:#1e293b; -webkit-text-size-adjust:100%; }}
  .report {{ max-width:720px; margin:0 auto; background:#ffffff; border-radius:16px;
             overflow:hidden; box-shadow:0 4px 18px rgba(15,23,42,.10); }}
  .inner {{ padding:26px; }}
  /* 헤더 배너 (h1) */
  .inner h1 {{ margin:-26px -26px 22px; padding:26px;
               background:#1e293b; background:linear-gradient(135deg,#1e293b,#4338ca);
               color:#ffffff; font-size:21px; line-height:1.3; letter-spacing:-.2px; }}
  /* 섹션 헤더 */
  .inner h2 {{ font-size:16.5px; margin:30px 0 12px; padding:6px 0 6px 12px;
               border-left:4px solid #6366f1; color:#0f172a; }}
  /* 종목/서브 카드 */
  .inner h3 {{ font-size:14.5px; margin:18px 0 10px; padding:11px 14px;
               background:#f8fafc; border:1px solid #e7ebf2; border-left:4px solid #6366f1;
               border-radius:10px; color:#1e293b; }}
  .inner p {{ margin:8px 0; font-size:13.5px; line-height:1.65; }}
  .inner ul {{ margin:8px 0; padding-left:20px; }}
  .inner ol {{ margin:8px 0; padding-left:22px; }}
  .inner ol li {{ font-size:12px; color:#475569; margin:3px 0; word-break:break-all; }}
  .inner a {{ color:#4338ca; text-decoration:none; }}
  .inner li {{ margin:4px 0; font-size:13.5px; line-height:1.6; }}
  .inner strong {{ color:#0f172a; }}
  /* 표 */
  .inner table {{ width:100%; border-collapse:collapse; margin:12px 0 6px; font-size:12.5px; }}
  .inner th {{ background:#1e293b; color:#fff; text-align:left; padding:9px 11px; font-weight:600; }}
  .inner td {{ padding:9px 11px; border-bottom:1px solid #eef2f7; vertical-align:top; line-height:1.5; }}
  .inner tr:nth-child(even) td {{ background:#f8fafc; }}
  /* 콜아웃(인용구) */
  .inner blockquote {{ margin:14px 0; padding:11px 15px; background:#eef2ff;
                       border-left:4px solid #6366f1; border-radius:10px;
                       color:#3730a3; font-size:13px; }}
  .inner blockquote p {{ margin:0; font-size:13px; }}
  .inner hr {{ border:none; border-top:1px solid #e7ebf2; margin:24px 0; }}
  .inner em {{ color:#64748b; }}
  img.macro {{ width:100%; height:auto; border:1px solid #e7ebf2; border-radius:12px; margin:6px 0; }}
  .foot {{ text-align:center; color:#94a3b8; font-size:11px; padding:14px; }}
</style></head>
<body>
  <div class="report"><div class="inner">
  {html_body}
  </div></div>
  <div class="foot">자동 생성 · {gen_time} · 정보 제공용(투자 권유 아님)</div>
</body></html>"""

    # related(이미지 포함) > alternative(plain/html) 구조
    root = MIMEMultipart("related")
    root["Subject"] = subject
    root["From"] = email_from
    root["To"] = ", ".join(email_to)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain_body, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    root.attach(alt)

    if chart_path and os.path.exists(chart_path):
        with open(chart_path, "rb") as f:
            img = MIMEImage(f.read())
        img.add_header("Content-ID", "<macro_chart>")
        img.add_header("Content-Disposition", "inline", filename="macro_dashboard.png")
        root.attach(img)

    print(f"[INFO] SMTP 연결 시도 → {smtp_host}:{smtp_port}")
    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(email_from, email_to, root.as_string())

    print(f"[OK] 발송 완료 → {', '.join(email_to)}")


def main() -> int:
    try:
        print("[1/3] 브리핑 생성 중 (웹서치 포함, 1~3분 소요될 수 있음)...")
        briefing = generate_briefing()
        print("[2/3] 거시 대시보드 생성 중...")
        chart_path, macro_rows = None, []
        try:
            chart_path, macro_rows = build_macro_dashboard("macro_dashboard.png")
        except Exception as e:  # noqa: BLE001  (그래프 실패해도 메일은 발송)
            print(f"[WARN] 거시 그래프 생성 실패(메일은 그대로 발송): {e}")
        print("[3/3] 이메일 발송 중...")
        send_email(briefing, chart_path, macro_rows)
        return 0
    except KeyError as e:
        print(f"[ERROR] 환경변수 누락: {e}. README의 설정 항목을 확인하세요.", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] 실패: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
