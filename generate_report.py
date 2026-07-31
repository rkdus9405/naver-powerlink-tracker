#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 파워링크 '월세카드결제' 순위변동 리포트 생성기.

rankings.csv를 읽어, 지정 기간의 '시각대별 순위 변동' 표(HTML)를 만든다.
- 관심 업체(자리페이·자리톡)는 별색으로 강조하고, 등장/순위변동을 상단 요약에 표시.
- 화살표: 직전 스냅샷 대비 ▲상승 / ▼하락 / NEW 신규 / – top10 밖.

사용 예:
  python generate_report.py --mode daily            # 어제(KST) 하루치
  python generate_report.py --from "2026-07-30 00:00" --to "2026-07-31 09:00"
  python generate_report.py --mode all              # 전체
옵션:
  --csv PATH   (기본 rankings.csv)
  --out PATH   (기본 report.html)
  --label STR  (리포트 부제)
"""
import csv, sys, argparse, subprocess, html
from datetime import datetime, timedelta

WATCH = ["자리페이", "자리톡"]   # 관심(별색) 업체
FMT = "%Y-%m-%d %H:%M"


def kst_now():
    """컨테이너 시계가 틀어질 수 있어, KST 현재시각을 date 명령으로 얻는다."""
    try:
        out = subprocess.check_output(["bash", "-lc", "TZ=Asia/Seoul date '+%Y-%m-%d %H:%M'"]).decode().strip()
        return datetime.strptime(out, FMT)
    except Exception:
        return datetime.now()


def load(csv_path):
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["_ts"] = datetime.strptime(r["체크시각(KST)"].strip(), FMT)
                r["_rank"] = int(str(r["순위"]).strip())
            except Exception:
                continue
            rows.append(r)
    return rows


def build(rows, t_from, t_to):
    sel = [r for r in rows if t_from <= r["_ts"] <= t_to]
    slots = sorted({r["_ts"] for r in sel})
    # 시각 -> {순위: 업체명}
    by_slot = {s: {} for s in slots}
    firm_copy = {}   # (업체) -> 최근 광고문구
    for r in sel:
        by_slot[r["_ts"]][r["_rank"]] = r["업체명"].strip()
        firm_copy[r["업체명"].strip()] = r["광고문구"].strip()
    # 업체 -> [슬롯별 순위 or None]
    firms = {}
    for i, s in enumerate(slots):
        rank2firm = by_slot[s]
        for rk, fm in rank2firm.items():
            firms.setdefault(fm, [None] * len(slots))
            firms[fm][i] = rk
    return slots, firms, firm_copy


def order_firms(slots, firms):
    n = len(slots)
    def key(item):
        fm, ranks = item
        last = None
        last_idx = -1
        for i in range(n - 1, -1, -1):
            if ranks[i] is not None:
                last = ranks[i]; last_idx = i; break
        present_last = ranks[n - 1] is not None
        # 마지막 슬롯에 있는 업체 우선(순위 오름차순), 그다음 이탈 업체(최근 등장 늦은 순)
        return (0 if present_last else 1, ranks[n - 1] if present_last else 99, -last_idx, last if last else 99)
    return sorted(firms.items(), key=key)


def watch_summary(slots, firms):
    """관심 업체 상태 요약 문자열 목록."""
    out = []
    for w in WATCH:
        match = [fm for fm in firms if w in fm]
        if not match:
            out.append((w, "미노출", "이 기간 top10에 없음"))
            continue
        for fm in match:
            ranks = firms[fm]
            seq = [(slots[i].strftime("%m-%d %H:%M"), ranks[i]) for i in range(len(slots)) if ranks[i] is not None]
            first_rank = seq[0][1]
            last_rank = seq[-1][1]
            trend = "→".join(str(r) for _, r in seq)
            note = f"{len(seq)}회 노출 · 순위 {trend}위 (최저 {min(r for _,r in seq)}위 / 최고 {max(r for _,r in seq)}위)"
            out.append((fm, f"{last_rank}위", note))
    return out


def cell_html(cur, prev, first_in_period):
    if cur is None:
        return '<td class="out">–</td>'
    mark = ""
    if prev is None and not first_in_period:
        mark = '<span class="new"> NEW</span>'
    elif prev is not None:
        if cur < prev:
            mark = f'<span class="up"> ▲{prev-cur}</span>'
        elif cur > prev:
            mark = f'<span class="down"> ▼{cur-prev}</span>'
    return f'<td><span class="rank">{cur}</span>{mark}</td>'


def render(slots, firms, firm_copy, title, label):
    ordered = order_firms(slots, firms)
    ncol = len(slots)
    ths = "".join(
        f'<th>{s.strftime("%m-%d")}<br>{s.strftime("%H:%M")}</th>' for s in slots
    )
    body = ""
    for fm, ranks in ordered:
        is_watch = any(w in fm for w in WATCH)
        is_lead = ranks[ncol - 1] == 1
        cls = []
        if is_watch: cls.append("watch")
        if is_lead: cls.append("lead")
        badge = ""
        if is_watch: badge = '<span class="wbadge">관심</span>'
        elif is_lead: badge = '<span class="badge">1위</span>'
        cells = f'<td class="name">{html.escape(fm)}{badge}</td>'
        for i in range(ncol):
            prev = None
            for j in range(i - 1, -1, -1):
                if ranks[j] is not None:
                    prev = ranks[j]; break
            first = all(ranks[j] is None for j in range(i))
            cells += cell_html(ranks[i], prev, first)
        body += f'<tr class="{" ".join(cls)}">{cells}</tr>'

    # 관심 업체 요약 박스
    wsum = watch_summary(slots, firms)
    wrows = "".join(
        f'<li><b style="color:#d9480f">{html.escape(fm)}</b> — <b>{st}</b> <span style="color:#6b7280">· {html.escape(note)}</span></li>'
        for fm, st, note in wsum
    )

    period = f'{slots[0].strftime("%Y-%m-%d %H:%M")} → {slots[-1].strftime("%m-%d %H:%M")}' if slots else "데이터 없음"
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{html.escape(title)}</title>
<style>
:root{{color-scheme:light dark}} *{{box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Malgun Gothic","Apple SD Gothic Neo",sans-serif;margin:0;padding:24px;background:#f6f7f9;color:#1a1a1a}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:20px;margin:0 0 4px}} .meta{{color:#6b7280;font-size:13px;margin-bottom:16px}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;font-size:14px}}
th,td{{padding:9px 7px;text-align:center;border-bottom:1px solid #eef0f2}}
th{{background:#f0f4ff;font-weight:700;font-size:12px;color:#1e3a8a}}
th.name,td.name{{text-align:left;padding-left:14px;font-weight:600;white-space:nowrap}}
tr:last-child td{{border-bottom:none}}
.rank{{font-weight:700}} .out{{color:#c0c4cc}}
.up{{color:#1d9a5b;font-size:11px}} .down{{color:#dc2626;font-size:11px}} .new{{color:#2563eb;font-size:11px;font-weight:700}}
.lead td{{background:#fffbe8}}
tr.watch td{{background:#fff0e9 !important}} tr.watch td.name{{color:#d9480f;border-left:3px solid #fd7e14}}
.badge{{display:inline-block;font-size:10px;background:#fde68a;color:#92400e;border-radius:5px;padding:1px 6px;margin-left:6px}}
.wbadge{{display:inline-block;font-size:10px;background:#fd7e14;color:#fff;border-radius:5px;padding:1px 6px;margin-left:6px}}
.box{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px 20px;margin-bottom:16px}}
.box.watch{{border:1px solid #fd7e14;background:#fff7f2}} .box h2{{font-size:15px;margin:0 0 10px}}
.box li{{margin:5px 0;line-height:1.5;font-size:13.5px}}
footer{{color:#6b7280;font-size:12px;margin-top:14px;line-height:1.6}}
</style></head><body><div class="wrap">
<h1>{html.escape(title)}</h1>
<div class="meta">{html.escape(label)} · 기간 {period} · 스냅샷 {ncol}회</div>
<div class="box watch"><h2>🔶 관심 업체 (자리페이 · 자리톡)</h2><ul>{wrows}</ul></div>
<table><thead><tr><th class="name">업체명</th>{ths}</tr></thead><tbody>{body}</tbody></table>
<footer>※ 파워링크는 접속 시점·환경에 따라 순위가 실시간 변동. 위는 rankings.csv 실제 수집분.<br>
※ 화살표=직전 스냅샷 대비 ▲상승/▼하락, NEW=신규 진입, –=해당 시각 top10 밖. 주황색 행=관심 업체.</footer>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="rankings.csv")
    ap.add_argument("--out", default="report.html")
    ap.add_argument("--mode", choices=["daily", "all", "range"], default="range")
    ap.add_argument("--from", dest="t_from", default=None)
    ap.add_argument("--to", dest="t_to", default=None)
    ap.add_argument("--title", default="네이버 파워링크 순위변동 — 월세카드결제")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    rows = load(a.csv)
    if not rows:
        print("no data", file=sys.stderr); sys.exit(2)

    now = kst_now()
    if a.mode == "daily":
        y = (now - timedelta(days=1)).date()
        t_from = datetime.combine(y, datetime.min.time())
        t_to = datetime.combine(y, datetime.max.time())
        label = a.label or f"일일 리포트 ({y})"
    elif a.mode == "all":
        t_from, t_to = min(r["_ts"] for r in rows), max(r["_ts"] for r in rows)
        label = a.label or "전체 기간"
    else:
        t_from = datetime.strptime(a.t_from, FMT) if a.t_from else min(r["_ts"] for r in rows)
        t_to = datetime.strptime(a.t_to, FMT) if a.t_to else now
        label = a.label or f"{t_from.strftime('%m-%d %H:%M')} ~ {t_to.strftime('%m-%d %H:%M')}"

    slots, firms, firm_copy = build(rows, t_from, t_to)
    if not slots:
        print("no rows in range", file=sys.stderr); sys.exit(3)
    htmlout = render(slots, firms, firm_copy, a.title, label)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(htmlout)
    print(f"wrote {a.out}: {len(slots)} slots, {len(firms)} firms")


if __name__ == "__main__":
    main()
