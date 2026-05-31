#!/usr/bin/env python3
"""
거시 지표 12개월 추이 대시보드 생성 모듈 (디자인 강화판)
------------------------------------------------------------
FRED(미 세인트루이스 연준, 무료 API)에서 미국·한국 핵심 지표의
최근 12개월 시계열을 받아 한 장의 PNG 대시보드로 그립니다.

- 무료 FRED API 키 필요: https://fredaccount.stlouisfed.org/apikeys
  환경변수 FRED_API_KEY 로 전달. (키가 없으면 None을 반환 → 메일은 그래프 없이 발송)
- 차트 라벨은 폰트 문제를 피하려고 영어로 표기합니다(메일 본문은 한국어).

반환: 생성된 PNG 파일 경로 (실패/키없음 시 None)
"""

import os
import json
import datetime
import urllib.request
import urllib.parse
from collections import OrderedDict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.patches import Polygon


FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# 주식과 직결되는 거시 지표 10종 (미국·한국). 'yoy'=전년동월대비%, 'level'=수준값.
# accent = 패널 강조색.
INDICATORS = [
    {"label": "US CPI (YoY %)",        "series": "CPIAUCSL",        "transform": "yoy",   "accent": "#6366f1"},
    {"label": "US Fed Funds (%)",      "series": "FEDFUNDS",        "transform": "level", "accent": "#0ea5e9"},
    {"label": "US 10Y Yield (%)",      "series": "DGS10",           "transform": "level", "accent": "#14b8a6"},
    {"label": "10Y-2Y Spread (%)",     "series": "T10Y2Y",          "transform": "level", "accent": "#8b5cf6"},
    {"label": "VIX (Volatility)",      "series": "VIXCLS",          "transform": "level", "accent": "#f43f5e"},
    {"label": "US HY Credit Spread(%)","series": "BAMLH0A0HYM2",    "transform": "level", "accent": "#f97316"},
    {"label": "WTI Crude ($/bbl)",     "series": "DCOILWTICO",      "transform": "level", "accent": "#eab308"},
    {"label": "Broad USD Index",       "series": "DTWEXBGS",        "transform": "level", "accent": "#22c55e"},
    {"label": "USD/KRW",               "series": "DEXKOUS",         "transform": "level", "accent": "#3b82f6"},
    {"label": "Korea CPI (YoY %)",     "series": "KORCPIALLMINMEI", "transform": "yoy",   "accent": "#ec4899"},
]


def _fred_fetch(series_id, start, api_key):
    params = urllib.parse.urlencode({
        "series_id": series_id, "api_key": api_key,
        "file_type": "json", "observation_start": start,
    })
    with urllib.request.urlopen(f"{FRED_BASE}?{params}", timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for obs in data.get("observations", []):
        v = obs.get("value", ".")
        if v in (".", "", None):
            continue
        try:
            out.append((obs["date"], float(v)))
        except ValueError:
            continue
    return out


def _monthlyize(observations):
    monthly = OrderedDict()
    for date, val in observations:
        monthly[date[:7]] = val
    return monthly


def _to_yoy(observations):
    monthly = _monthlyize(observations)
    yoy = []
    for ym in monthly:
        y, m = ym.split("-")
        prev = f"{int(y) - 1}-{m}"
        if prev in monthly and monthly[prev]:
            yoy.append((ym, round((monthly[ym] / monthly[prev] - 1.0) * 100.0, 2)))
    return yoy


def _series_for(indicator, api_key):
    start = (datetime.date.today() - datetime.timedelta(days=800)).isoformat()
    raw = _fred_fetch(indicator["series"], start, api_key)
    if not raw:
        return [], []
    pairs = _to_yoy(raw) if indicator["transform"] == "yoy" else list(_monthlyize(raw).items())
    pairs = pairs[-12:]
    return [p[0][2:] for p in pairs], [p[1] for p in pairs]


def _gradient_fill(ax, x, y, color):
    """라인 아래를 위→아래로 페이드되는 그라데이션으로 채움."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    ax.plot(x, y, color=color, lw=2.4, zorder=4, solid_capstyle="round", solid_joinstyle="round")
    ymin, ymax = float(y.min()), float(y.max())
    if ymin == ymax:
        ymin -= 1; ymax += 1
    grad = np.empty((100, 1, 4))
    grad[:, :, :3] = to_rgb(color)
    grad[:, :, -1] = np.linspace(0.30, 0.0, 100)[:, None]
    im = ax.imshow(grad, aspect="auto", origin="upper",
                   extent=[x.min(), x.max(), ymin, ymax], zorder=2)
    verts = np.vstack([np.column_stack([x, y]), [x.max(), ymin], [x.min(), ymin]])
    clip = Polygon(verts, closed=True, facecolor="none", edgecolor="none")
    ax.add_patch(clip)
    im.set_clip_path(clip)


def _draw_panel(ax, label, labels, values, accent):
    ax.set_facecolor("#fbfcfe")
    x = list(range(len(values)))
    _gradient_fill(ax, x, values, accent)

    # 마지막 포인트 강조 + 값 배지
    ax.scatter([x[-1]], [values[-1]], s=42, color=accent, zorder=5,
               edgecolors="white", linewidths=1.5)
    ax.annotate(f"{values[-1]:g}", xy=(x[-1], values[-1]), xytext=(0, 12),
                textcoords="offset points", ha="center", fontsize=9.5,
                fontweight="bold", color="white", zorder=6,
                bbox=dict(boxstyle="round,pad=0.32", fc=accent, ec="none"))

    # 제목 + 12개월 변화(방향 배지)
    ax.set_title(label, loc="left", fontsize=11, fontweight="bold",
                 color="#0f172a", pad=14)
    delta = values[-1] - values[0]
    up = delta >= 0
    ax.text(0.995, 1.06, f"{'▲' if up else '▼'} {delta:+.2f}",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=9,
            fontweight="bold", color="#ef4444" if up else "#10b981")

    # 축/그리드 정리
    ax.grid(True, axis="y", alpha=0.18, linewidth=0.8)
    ax.grid(False, axis="x")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#cbd5e1")
    step = max(1, len(labels) // 5)
    ax.set_xticks([i for i in range(0, len(labels), step)])
    ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)],
                       fontsize=7.5, color="#64748b")
    ax.tick_params(axis="y", labelsize=7.5, colors="#64748b", length=0)
    pad = (max(values) - min(values)) * 0.18 or 1
    ax.set_ylim(min(values) - pad, max(values) + pad * 1.6)
    ax.set_xlim(-0.4, len(values) - 0.6)


def build_macro_dashboard(output_path="macro_dashboard.png"):
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("[INFO] FRED_API_KEY 없음 → 거시 그래프 생략")
        return None

    panels = []
    for ind in INDICATORS:
        try:
            labels, values = _series_for(ind, api_key)
            if values:
                panels.append((ind["label"], labels, values, ind["accent"]))
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] {ind['series']} 실패: {e}")
    if not panels:
        print("[WARN] 그릴 데이터 없음")
        return None

    cols = 2
    rows = (len(panels) + cols - 1) // cols
    plt.rcParams["font.family"] = "DejaVu Sans"
    fig, axes = plt.subplots(rows, cols, figsize=(11.5, 2.9 * rows), facecolor="white")
    axes = np.atleast_1d(axes).flatten()

    for ax, panel in zip(axes, panels):
        _draw_panel(ax, *panel)
    for ax in axes[len(panels):]:
        ax.axis("off")

    today = datetime.date.today().isoformat()
    fig.suptitle("MACRO DASHBOARD  ·  12-Month Trend", x=0.012, y=0.998,
                 ha="left", fontsize=15, fontweight="bold", color="#0f172a")
    fig.text(0.012, 0.969, f"as of {today}  ·  Source: FRED  ·  ▲/▼ = 12M change (directional)",
             ha="left", fontsize=8.5, color="#94a3b8")
    fig.tight_layout(rect=[0, 0.005, 1, 0.95])
    fig.subplots_adjust(hspace=0.6, wspace=0.18)
    fig.savefig(output_path, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[OK] 거시 대시보드 생성 → {output_path}")
    return output_path


if __name__ == "__main__":
    build_macro_dashboard()
