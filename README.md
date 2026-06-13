# 📈 데일리 투자 브리핑 자동화

매일 아침, Claude가 직접 뉴스·Stocktwits 트렌딩을 조사해 한국·미국 시장 브리핑을
작성하고 **이메일로 자동 발송**합니다. GitHub Actions에 올리면 컴퓨터를 켜두지 않아도
무인으로 돌아갑니다.

> ⚠️ 이 도구가 생성하는 내용은 정보 정리이며 투자 권유·자문이 아닙니다.
> 종목은 강세/약세 논리를 함께 제시할 뿐, 최종 판단과 책임은 본인에게 있습니다.

---

## 구성 파일
- `daily_briefing.py` — 브리핑 생성 + 이메일 발송 본체
- `macro_charts.py` — 거시 지표 12개월 추이 대시보드(PNG) 생성
- `requirements.txt` — 파이썬 의존성
- `.github/workflows/daily-briefing.yml` — 매일 자동 실행 스케줄
- `sample_report_preview.html` — 메일에 도착할 리포트 디자인 미리보기(브라우저로 열어보기)
- `sample_macro_dashboard.png` — 거시 대시보드 디자인 미리보기

## 리포트에 담기는 내용
매일 메일 한 통에 다음이 순서대로 들어갑니다(웹서치로 그날 데이터를 채움):

1. **⚡ 3분 요약** — 미국·한국 시장 한 줄 + 핵심 포인트
2. **📊 더보기** — 미국 증시 / 한국 증시 / 거시·지정학 / 산업 구조 / 다음 일정(요일별 캘린더 표)
3. **🏦 거시 지표 대시보드** — 현재값 / 3·6·12개월 평균 표(흐름 확인) + 12개월 추이 그래프(10지표 이미지)
4. **🏭 산업 섹터 분석** — 11개 섹터(반도체·보안·전통제조·드론·크립토·에너지·전력·은행·핀테크·바이오·식품)
   × [모멘텀 · 핵심이슈 2~3개 · 저평가/과매도 후보 1종] + 표 아래 '핵심 이슈 근거'
5. **🔬 오늘의 주목 종목 (2~3개)** — 위 워치리스트+트렌딩에서 선별, 최소 1개는 저평가/과매도 후보.
   종목당 [사업모델(매출원·비용구조·핵심고객) · 촉매 · 실적/밸류 · 강세 · 약세 · 체크포인트 · 밸류/과열 진단]
6. **🎯 자기계발 한 스푼** — 그날의 투자·사고법 인사이트
7. **📎 출처** — 그날 웹서치가 참고한 실제 소스 링크 목록

## 📊 거시 지표 대시보드
`FRED_API_KEY`를 등록하면, 메일 안에 **최근 12개월 추이 그래프**가 디자인된 이미지로 박힙니다.
주식과 직결되는 지표 10종으로 위험선호·금리·물가·환율·에너지를 한눈에 봅니다:

| 지표 | FRED 시리즈 | 왜 보나 |
|---|---|---|
| 미국 CPI (YoY) | CPIAUCSL | 인플레 방향 |
| 미국 기준금리 | FEDFUNDS | 통화정책 |
| 미국 10년물 금리 | DGS10 | 할인율·밸류에이션 |
| 장단기 금리차(10Y-2Y) | T10Y2Y | 경기침체 신호 |
| VIX | VIXCLS | 시장 공포·변동성 |
| 하이일드 신용스프레드 | BAMLH0A0HYM2 | 위험선호·신용경색 |
| WTI 유가 | DCOILWTICO | 에너지·인플레 압력 |
| 달러인덱스(광의) | DTWEXBGS | 글로벌 유동성·수출주 |
| 원/달러 | DEXKOUS | 외국인 수급·환율 |
| 한국 CPI (YoY) | KORCPIALLMINMEI | 국내 물가 |

지표를 바꾸려면 `macro_charts.py`의 `INDICATORS` 리스트만 수정하면 됩니다
(지수형 지표는 `"transform": "yoy"`, 금리·환율처럼 수준값이면 `"level"`).
키를 등록하지 않으면 그래프 없이 텍스트 브리핑만 정상 발송됩니다.

---

## 준비물 (한 번만 세팅)

### 1) Anthropic API 키
1. https://console.anthropic.com 에서 가입 후 **API key** 발급
2. 결제 수단 등록 (웹서치 포함 1회 실행 비용은 보통 수백 원 안팎. 모델·검색량에 따라 변동)
3. Console 설정에서 **웹서치(Web Search)** 사용이 켜져 있는지 확인

### 2) 보낼 이메일 (Gmail 기준)
Gmail은 일반 비밀번호가 아니라 **앱 비밀번호**가 필요합니다.
1. 구글 계정에 2단계 인증 활성화
2. https://myaccount.google.com/apppasswords 에서 앱 비밀번호 생성(16자리)
3. 이 16자리를 `SMTP_PASSWORD`로 사용 (네이버·다음 등 다른 메일은 해당 SMTP 정보로 교체)

### 3) GitHub 저장소
1. 새 저장소(예: `daily-briefing`)를 만들고 이 폴더의 파일을 그대로 올립니다
   (`.github/workflows/` 폴더 구조 유지)
2. 저장소 **Settings → Secrets and variables → Actions → New repository secret**
   에서 아래 값을 등록:

| Secret 이름 | 값 (예시) |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | 보내는 Gmail 주소 |
| `SMTP_PASSWORD` | 위에서 만든 16자리 앱 비밀번호 |
| `EMAIL_FROM` | 보내는 Gmail 주소 |
| `EMAIL_TO` | 받을 주소 (쉼표로 여러 개 가능) |
| `FRED_API_KEY` | (선택) 거시 그래프용. https://fredaccount.stlouisfed.org/apikeys 에서 무료 발급 |

---

## 실행

### 바로 테스트
저장소 **Actions 탭 → Daily Investment Briefing → Run workflow** 를 누르면
지금 즉시 한 번 실행됩니다. 메일이 잘 오는지 먼저 확인하세요.

### 자동 실행 시간 바꾸기
`.github/workflows/daily-briefing.yml`의 cron 값만 고치면 됩니다.
GitHub의 cron은 **UTC 기준**이라 한국시간(KST) = UTC + 9시간입니다.

- `0 22 * * 0-4` → **평일 아침 7시 (KST)** ← 기본값
- `0 23 * * 0-4` → 평일 아침 8시 (KST)
- `30 21 * * 0-4` → 평일 아침 6시 30분 (KST)

(`0-4`는 UTC 기준 일~목이라 KST로는 월~금 아침에 도착합니다.)

---

## 내 컴퓨터에서 직접 돌려보기 (선택)
```bash
pip install -r requirements.txt

export ANTHROPIC_API_KEY="sk-ant-..."
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="465"
export SMTP_USER="you@gmail.com"
export SMTP_PASSWORD="앱비밀번호16자리"
export EMAIL_FROM="you@gmail.com"
export EMAIL_TO="you@gmail.com"

python daily_briefing.py
```

---

## 커스터마이즈 (환경변수)
| 변수 | 기본값 | 설명 |
|---|---|---|
| `BRIEFING_MODEL` | `claude-sonnet-4-6` | 품질 더 원하면 `claude-opus-4-8` |
| `WEB_SEARCH_TOOL` | `web_search_20250305` | 최신 동적필터판은 `web_search_20260209` |
| `BRIEFING_MAX_SEARCHES` | `18` | 1회 실행 시 검색 허용 횟수(비용에 영향) |
| `BRIEFING_MAX_TOKENS` | `8000` | 브리핑 최대 길이 |
| `BRIEFING_STOCK_PICKS` | `2~3` | 그날 상세분석할 종목 수 |
| `BRIEFING_TZ` | `Asia/Seoul` | 날짜·시간 기준 시간대 |

---

## 자주 막히는 곳
- **메일이 안 와요** → 스팸함 확인. Gmail은 일반 비번이 아니라 *앱 비밀번호*여야 함.
- **`web search not enabled` 오류** → Anthropic Console에서 웹서치 기능 켜기.
- **시간이 안 맞아요** → cron은 UTC. KST는 +9시간이라는 점 기억.
- **GitHub Actions가 안 돌아요** → 무료 계정은 일정이 몇 분~수십 분 지연될 수 있음(정상).
  공개 저장소면 무료 사용량이 넉넉하고, 비공개 저장소도 월 무료 한도 안에서 충분합니다.

---

# 🌐 웹 플랫폼 (상시 대시보드 + 채팅 + 전망)

이메일에 더해, **언제든 접속하는 웹 대시보드**로도 쓸 수 있습니다. 하나의 FastAPI 앱이
대시보드·아카이브·실시간 Q&A·3/6개월 전망을 제공하고, **내장 스케줄러**가 매일 브리핑을
생성→저장→이메일 발송하고 매월 1일 전망을 만듭니다.

## 추가 파일
- `app.py` — 웹앱(대시보드·채팅·전망·스케줄러)
- `outlook.py` — 3개월/6개월 시나리오 전망 생성기
- `render.yaml` — Render 배포 설정(영구 디스크 포함)

## 페이지
- **📊 대시보드** — 최신 브리핑 + 왼쪽에 날짜별 지난 브리핑
- **💬 물어보기** — 질문 입력 → 최신 브리핑 기반 + 웹서치로 즉시 답변
- **🔮 전망** — 3/6개월 시나리오(매월 1일 자동, 버튼으로 즉시 생성도 가능)

## Render 배포 (어디서나 접속)
1. 이 저장소를 GitHub에 올린 상태에서 https://render.com 가입(GitHub 연동).
2. **New + → Blueprint → 이 저장소 선택** → `render.yaml`이 자동 인식됩니다.
3. 배포 중 환경변수 입력 화면에서 값 채우기(Secret과 동일):
   `ANTHROPIC_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
   `EMAIL_FROM`, `EMAIL_TO`, (선택) `FRED_API_KEY`.
   - **사이트 잠그기(권장):** `APP_USER`, `APP_PASSWORD`도 넣으면 접속 시 비밀번호를 물어봅니다.
     (인터넷에 공개되므로 설정 권장)
4. 배포 완료되면 `https://<앱이름>.onrender.com` 으로 접속. 첫 화면에서 "지금 브리핑 생성"으로 테스트.

> 비용: 영구 디스크 + 상시 가동을 위해 Render **Starter(유료, 소액)** 플랜을 씁니다.
> 데이터(브리핑·전망·그래프)는 `/var/data` 영구 디스크에 SQLite로 보존돼 재배포에도 남습니다.

## 이메일과 동시 사용
- 웹앱의 내장 스케줄러가 매일 브리핑을 만들고 **이메일도 발송**합니다.
- 따라서 기존 **GitHub Actions 워크플로(daily-briefing.yml)는 꺼두는 걸 권장**합니다(중복 발송·중복 비용 방지).
  - 끄는 법: 저장소 Actions 탭 → 해당 워크플로 → 우상단 ⋯ → **Disable workflow**.
- 발송 시각은 `DAILY_HOUR`/`DAILY_MINUTE`(기본 06:13, `BRIEFING_TZ` 기준)로 조정.

## 로컬에서 먼저 돌려보기 (선택)
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=... SMTP_USER=... SMTP_PASSWORD=... EMAIL_FROM=... EMAIL_TO=...
export DATA_DIR=./data BRIEFING_TZ=America/Chicago
uvicorn app:app --reload
# 브라우저에서 http://localhost:8000
```
