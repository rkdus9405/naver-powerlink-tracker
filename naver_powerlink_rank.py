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
#  1) '파워링크' 헤더를 찾고, 그 근처의 리스트(li) 컨테이너를 파워링크 영역으로 본다.
#  2) 못 찾으면 네이버 광고 리다이렉트 링크(adcr.naver.com) 기준으로 컨테이너를 역추적한다.
#  3) 광고 항목은 '최상위 li'만 센다. 광고 밑에 붙는 확장 태그(최저수수료/무이자할부 등)는
#     광고 li 안에 '중첩된 li'라서 별도 순위로 세면 안 된다 → 중첩 li는 제외한다.
#  4) 각 광고에서 이미지/도메인/원문을 뽑고, 업체명·광고문구는 파이썬에서 후처리한다.
JS_EXTRACT = r"""
() => {
  const T = el => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const domainRe = /([a-z0-9-]+\.)+(com|co\.kr|kr|net|io|shop|me|app|biz|org|kro\.kr)(\/[^\s]*)?/i;

  // 1) '파워링크' 헤더 기준으로 영역 찾기
  let section = null;
  const leaves = Array.from(document.querySelectorAll('h2,h3,span,strong,a,div'))
    .filter(el => T(el) === '파워링크');
  if (leaves.length) {
    let node = leaves[0];
    for (let i = 0; i < 8 && node; i++) {
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

  const res = { sectionText: '', items: [] };
  if (!section) return res;

  const allLis = Array.from(section.querySelectorAll('li'));
  // 최상위 광고 li만: section 안에서 다른 li에 중첩되지 않은 것 + 광고처럼 '실질적'인 것
  const adLis = allLis.filter(li => {
    let p = li.parentElement;
    while (p && p !== section) { if (p.tagName === 'LI') return false; p = p.parentElement; }
    const t = T(li);
    return !!(li.querySelector('img') || t.length > 25 || domainRe.test(t));
  });

  const seen = new Set();
  adLis.forEach(li => {
    const raw = T(li);
    if (!raw || seen.has(raw)) return;
    seen.add(raw);
    // 이미지: searchad 이미지형 우선, 없으면 첫 img(파비콘 포함)
    const imgs = Array.from(li.querySelectorAll('img'));
    const big = imgs.find(im => /searchad-phinf/.test(im.src || ''));
    const imageUrl = big ? big.src : (imgs[0] ? (imgs[0].src || '') : '');
    // 도메인: 링크 텍스트에서 먼저, 없으면 원문에서
    let domain = '';
    for (const a of Array.from(li.querySelectorAll('a'))) {
      const m = T(a).match(domainRe);
      if (m) { domain = m[0]; break; }
    }
    if (!domain) { const m = raw.match(domainRe); if (m) domain = m[0]; }
    res.items.push({ raw, imageUrl, domain });
  });
  return res;
}
"""


def parse_item(it):
    """원문(raw)과 도메인으로 업체명·광고문구를 분리한다.
    네이버 파워링크 원문 순서: [브랜드명] [도메인] [제목/설명 ...]"""
    raw = (it.get("raw") or "").strip()
    domain = (it.get("domain") or "").strip()
    brand, ad_copy = "", raw
    if domain and domain in raw:
        before, after = raw.split(domain, 1)
        brand = before.replace("네이버 로그인", "").strip(" -|·")
        ad_copy = after.strip(" -|·")
    company = brand or domain
    return company, ad_copy


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

            data = page.evaluate(JS_EXTRACT)
            items = data.get("items", []) if isinstance(data, dict) else []
            ts = now_kst()
            for i, it in enumerate(items, start=1):
                company, ad_copy = parse_item(it)
                rows.append(
                    [ts, KEYWORD, i, company, ad_copy, it.get("imageUrl", ""), it.get("raw", "")]
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
