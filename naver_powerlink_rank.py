#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 파워링크 순위 추적기 — '월세카드결제'
- 네이버 통합검색 페이지를 열어 '파워링크' 광고 영역에서
  순위 / 업체명 / 광고문구 / 이미지(URL)를 추출해 rankings.csv 에 누적한다.
- 처음 몇 번은 '보정(calibration)' 단계다. 실행 후 debug_page.html 과
  debug_screenshot.png 를 확인해 셀렉터를 정교화한다.
- 데이터는 절대 지어내지 않는다. 화면에서 실제로 읽은 것만 기록한다.
"""

import csv
import os
import sys
import datetime
import traceback

from playwright.sync_api import sync_playwright

# ── 설정 ─────────────────────────────────────────────────────────────
KEYWORD = "월세카드결제"
URL = (
    "https://search.naver.com/search.naver?ie=UTF-8&sm=whl_hty"
    "&query=%EC%9B%94%EC%84%B8%EC%B9%B4%EB%93%9C%EA%B2%B0%EC%A0%9C"
)
CSV_PATH = "rankings.csv"
HEADER = ["체크시각(KST)", "키워드", "순위", "업체명", "광고문구", "이미지URL", "원문(raw)"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def now_kst() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime(
        "%Y-%m-%d %H:%M"
    )


# ── 파워링크 추출용 JavaScript (브라우저 컨텍스트에서 실행) ──────────────
# 전략:
#  1) '파워링크' 라는 텍스트를 가진 헤더를 찾고, 그 근처의 리스트(li) 컨테이너를 파워링크 영역으로 본다.
#  2) 못 찾으면, 네이버 광고 클릭 리다이렉트 도메인(adcr.naver.com) 링크를 기준으로 컨테이너를 역추적한다.
#  3) 각 광고(li)에서 이미지/제목/원문 텍스트를 best-effort로 뽑는다.
#     ※ 업체명·광고문구의 정확한 분리는 실제 HTML 확인 후 보정한다. 원문(raw)이 안전망이다.
JS_EXTRACT = r"""
() => {
  const T = el => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();

  // 1) '파워링크' 헤더 기준으로 영역 찾기
  let section = null;
  const leaves = Array.from(document.querySelectorAll('h2,h3,span,strong,a,div'))
    .filter(el => T(el) === '파워링크');
  if (leaves.length) {
    let node = leaves[0];
    for (let i = 0; i < 7 && node; i++) {
      node = node.parentElement;
      if (node && node.querySelectorAll('li').length >= 1) { section = node; break; }
    }
  }

  // 2) fallback: 네이버 광고 리다이렉트 링크 기준
  if (!section) {
    const a = document.querySelector('a[href*="adcr.naver.com"], a[href*="ader.naver.com"]');
    if (a) {
      let node = a;
      for (let i = 0; i < 9 && node; i++) {
        node = node.parentElement;
        if (node && node.querySelectorAll('li').length >= 2) { section = node; break; }
      }
    }
  }

  const out = [];
  if (!section) return out;

  // 광고 항목 후보: 링크를 가진 li
  let lis = Array.from(section.querySelectorAll('li')).filter(li => li.querySelector('a'));
  // li 구조가 아니면, 광고 링크의 직접 부모 블록을 항목으로
  if (lis.length === 0) {
    const anchors = Array.from(section.querySelectorAll('a[href*="adcr.naver.com"], a[href*="ader.naver.com"]'));
    lis = anchors.map(a => a.closest('div,li,article') || a.parentElement).filter(Boolean);
  }

  const seen = new Set();
  lis.forEach(li => {
    const raw = T(li);
    if (!raw || seen.has(raw)) return;
    seen.add(raw);
    const img = li.querySelector('img');
    let imageUrl = img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '';
    // 가장 긴 링크 텍스트를 제목(광고문구 헤드라인) 후보로
    const links = Array.from(li.querySelectorAll('a')).map(T).filter(Boolean);
    let title = links.length ? links.sort((a, b) => b.length - a.length)[0] : '';
    // 표시 도메인/사이트명 후보 (짧은 텍스트 중 도메인처럼 생긴 것)
    const siteCand = links.find(t => /\.[a-z]{2,}/i.test(t) && t.length < 40) || '';
    out.push({ raw, imageUrl, title, site: siteCand });
  });
  return out;
}
"""


def scrape():
    rows = []
    error = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--lang=ko-KR"])
        ctx = browser.new_context(
            locale="ko-KR",
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 2600},
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        page = ctx.new_page()
        try:
            page.goto(URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)
            # 파워링크 '더보기'가 있으면 펼침 시도 (있을 때만)
            try:
                more = page.query_selector("a:has-text('더보기')")
                if more:
                    more.click()
                    page.wait_for_timeout(1500)
            except Exception:
                pass

            # 디버그 아티팩트 저장 (보정용)
            try:
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
                page.screenshot(path="debug_screenshot.png", full_page=True)
            except Exception:
                pass

            items = page.evaluate(JS_EXTRACT)
            ts = now_kst()
            for i, it in enumerate(items, start=1):
                company = (it.get("site") or "").strip()
                raw = (it.get("raw") or "").strip()
                title = (it.get("title") or "").strip()
                # 광고문구: 제목이 raw 안에 있으면 raw 전체를, 아니면 title 사용
                ad_copy = raw if raw else title
                rows.append(
                    [ts, KEYWORD, i, company, ad_copy, it.get("imageUrl", ""), raw]
                )
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            browser.close()
    return rows, error


def append_csv(rows):
    new_file = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        w.writerows(rows)


def main():
    rows, error = scrape()
    ts = now_kst()
    if error:
        # 접속/추출 실패도 한 줄 기록 (조용히 실패하지 않는다)
        append_csv([[ts, KEYWORD, 0, "", f"수집실패: {error}", "", ""]])
        print(f"[{ts}] 수집 실패: {error}")
        # 실패해도 워크플로우는 계속 진행(커밋되도록) — 종료코드 0
        return
    if not rows:
        append_csv([[ts, KEYWORD, 0, "", "이 시각 파워링크 광고 없음(또는 영역 미검출)", "", ""]])
        print(f"[{ts}] 파워링크 항목 0건 — debug_page.html 을 확인해 보정 필요")
        return
    append_csv(rows)
    print(f"[{ts}] 파워링크 {len(rows)}건 기록 완료")
    for r in rows:
        print(f"  {r[2]}위 | {r[3][:20]} | {r[4][:40]}")


if __name__ == "__main__":
    main()
