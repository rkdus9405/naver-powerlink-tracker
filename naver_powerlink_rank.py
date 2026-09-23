#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 파워링크 순위 추적기 — '월세카드결제'  (PC + 모바일)

- PC(search.naver.com)와 모바일(m.search.naver.com)을 각각 열어
  파워링크 광고 영역에서 순위 / 업체명 / 광고문구 / 이미지(URL)를 추출해
  rankings.csv 에 누적한다. 한 회차에 PC 줄과 모바일 줄이 나란히 쌓인다.
- 데이터는 절대 지어내지 않는다. 화면에서 실제로 읽은 것만 기록한다.
- 중점 추적 업체가 목록에 없으면 순위 0 / '미노출' 로 한 줄 남긴다.
  ("기록이 없다"와 "확인했는데 없었다"는 다른 정보이므로 구분한다.)
"""

import csv
import os
import datetime
import traceback

from playwright.sync_api import sync_playwright

# ── 설정 ─────────────────────────────────────────────────────────────
KEYWORD = "월세카드결제"
Q = "%EC%9B%94%EC%84%B8%EC%B9%B4%EB%93%9C%EA%B2%B0%EC%A0%9C"  # 월세카드결제

CSV_PATH = "rankings.csv"
OLD_CSV_PATH = "rankings_old.csv"
HEADER = ["체크시각(KST)", "기기", "키워드", "순위", "업체명", "광고문구", "이미지URL", "원문(raw)"]

# 목록에 없을 때 '미노출' 줄을 남길 업체
WATCH = ["단비페이", "자리페이", "사장님페이"]

UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

DEVICES = [
    {
        "name": "PC",
        "url": f"https://search.naver.com/search.naver?ie=UTF-8&sm=whl_hty&query={Q}",
        "ua": UA_PC,
        "viewport": {"width": 1280, "height": 2600},
        "is_mobile": False,
        "has_touch": False,
        "debug_html": "debug_page.html",
        "debug_png": "debug_screenshot.png",
    },
    {
        "name": "모바일",
        "url": f"https://m.search.naver.com/search.naver?ie=UTF-8&sm=mtp_hty&query={Q}",
        "ua": UA_MOBILE,
        "viewport": {"width": 390, "height": 2200},
        "is_mobile": True,
        "has_touch": True,
        "debug_html": "debug_page_mobile.html",
        "debug_png": "debug_screenshot_mobile.png",
    },
]


def now_kst() -> str:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime(
        "%Y-%m-%d %H:%M"
    )


# ── 파워링크 추출용 JavaScript (브라우저 컨텍스트에서 실행) ──────────────
# 전략:
#  1) '파워링크' 헤더를 찾고, 그 근처의 리스트(li) 컨테이너를 파워링크 영역으로 본다.
#  2) 못 찾으면 네이버 광고 리다이렉트 링크(adcr.naver.com) 기준으로 컨테이너를 역추적한다.
#  3) 광고 항목은 '최상위 li'만 센다. 광고 밑에 붙는 확장 태그(최저수수료/무이자할부 등)는
#     광고 li 안에 '중첩된 li'라서 별도 순위로 세면 안 된다 → 중첩 li는 제외한다.
#  4) li 구조가 아닌 모바일 레이아웃 대비: li가 하나도 안 잡히면 광고 링크의
#     공통 조상 블록들을 항목으로 본다.
JS_EXTRACT = r"""
() => {
  const T = el => ((el && el.innerText) || '').replace(/\s+/g, ' ').trim();
  const domainRe = /([a-z0-9-]+\.)+(com|co\.kr|kr|net|io|shop|me|app|biz|org|kro\.kr)(\/[^\s]*)?/i;
  const AD = 'a[href*="adcr."], a[href*="ader."], a[href*="/crd/rd"], a[href*="ad.search.naver"]';

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
  if (!section) {
    const a = document.querySelector(AD);
    if (a) {
      let node = a;
      for (let i = 0; i < 9 && node; i++) {
        node = node.parentElement;
        if (node && node.querySelectorAll('li').length >= 2) { section = node; break; }
      }
      if (!section) {
        for (let i = 0, node2 = a; i < 9 && node2; i++) {
          node2 = node2.parentElement;
          if (node2 && node2.querySelectorAll(AD).length >= 2) { section = node2; break; }
        }
      }
    }
  }

  const res = { items: [], mode: '', diag: {} };
  const hosts = {};
  Array.from(document.querySelectorAll('a[href]')).forEach(a => {
    try { const h = new URL(a.href, location.href).hostname; hosts[h] = (hosts[h] || 0) + 1; } catch (e) {}
  });
  res.diag.hosts = Object.entries(hosts).sort((x, y) => y[1] - x[1]).slice(0, 12);
  res.diag.adAnchors = document.querySelectorAll(AD).length;
  res.diag.chains = Array.from(document.querySelectorAll(AD)).filter((a, i) => i % 7 === 0).slice(0, 5)
    .map(a => { const c = []; let n = a; for (let i = 0; i < 8 && n; i++) { c.push(n.tagName + '.' + ((n.className || '').toString().split(' ')[0] || '-').slice(0, 16)); n = n.parentElement; } return c.join(' < '); });
  res.diag.hasHeader = leaves.length;
  if (!section) { res.diag.section = 'NOT_FOUND'; return res; }
  res.diag.section = section.tagName + '.' + (section.className || '').toString().slice(0, 60) + ' li=' + section.querySelectorAll('li').length;
  res.diag.outline = Array.from(section.children).slice(0, 8).map(c => c.tagName + '.' + (c.className || '').toString().slice(0, 30) + '[' + (c.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40) + ']');

  const pick = [];
  const allLis = Array.from(section.querySelectorAll('li'));
  const topLis = allLis.filter(li => {
    let p = li.parentElement;
    while (p && p !== section) { if (p.tagName === 'LI') return false; p = p.parentElement; }
    const t = T(li);
    return domainRe.test(t) && t.length > 20;
  });
  if (topLis.length >= 2) {
    res.mode = 'li';
    pick.push(...topLis);
  } else {
    // 모바일 등 li가 아닌 구조: 광고 링크마다 '의미 있는 블록'까지 올라간다
    res.mode = 'block';
    const hasDom = el => domainRe.test(T(el));
    const anchors = Array.from(document.querySelectorAll(AD));
    const cand = [];
    anchors.forEach(a => {
      let li = a.closest('li');
      for (let i = 0; i < 5 && li && !hasDom(li); i++) {
        li = li.parentElement ? li.parentElement.closest('li') : null;
      }
      if (li && hasDom(li)) { if (cand.indexOf(li) < 0) cand.push(li); return; }
      let m = a;
      for (let i = 0; i < 10 && m && m.parentElement && !hasDom(m); i++) m = m.parentElement;
      if (m && hasDom(m) && cand.indexOf(m) < 0) cand.push(m);
    });
    const tops = cand.filter(el => !cand.some(o => o !== el && el.contains(o)));
    pick.push(...tops);
    res.parentTag = 'anchors=' + anchors.length + ' cand=' + cand.length + ' tops=' + tops.length;
  }

  const seen = new Set();
  pick.forEach(el => {
    const raw = T(el);
    if (!raw || seen.has(raw)) return;
    seen.add(raw);
    const imgs = Array.from(el.querySelectorAll('img'));
    const big = imgs.find(im => /searchad-phinf/.test(im.src || ''));
    const imageUrl = big ? big.src : (imgs[0] ? (imgs[0].src || '') : '');
    let domain = '';
    for (const a of Array.from(el.querySelectorAll('a'))) {
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
        brand = before.replace("네이버 로그인", "").replace("네이버로그인", "").strip(" -|·")
        ad_copy = after.strip(" -|·")
    company = brand or domain
    return company, ad_copy


def scrape_device(p, dev):
    """기기 하나를 수집한다. (rows, error) 반환"""
    rows, error = [], None
    browser = p.chromium.launch(headless=True, args=["--lang=ko-KR"])
    ctx = browser.new_context(
        locale="ko-KR",
        user_agent=dev["ua"],
        viewport=dev["viewport"],
        is_mobile=dev["is_mobile"],
        has_touch=dev["has_touch"],
        device_scale_factor=3 if dev["is_mobile"] else 1,
        extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
    )
    page = ctx.new_page()
    try:
        page.goto(dev["url"], wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        # 모바일은 지연 로딩이 있어 한 번 내려준다
        if dev["is_mobile"] and len(items) < 3:
            for _ in range(3):
                page.mouse.wheel(0, 900)
                page.wait_for_timeout(600)
            page.mouse.wheel(0, -2700)
            page.wait_for_timeout(800)
        try:
            more = page.query_selector("a:has-text('더보기')")
            if more:
                more.click()
                page.wait_for_timeout(1500)
        except Exception:
            pass

        try:
            with open(dev["debug_html"], "w", encoding="utf-8") as f:
                f.write(page.content())
            page.screenshot(path=dev["debug_png"], full_page=True)
        except Exception:
            pass

        data = page.evaluate(JS_EXTRACT)
        items = data.get("items", []) if isinstance(data, dict) else []
        mode = data.get("mode", "") if isinstance(data, dict) else ""
        diag = data.get("diag", {}) if isinstance(data, dict) else {}
        if dev["is_mobile"]:
            print(f"  [{dev['name']}] -- 구조 진단 --")
            print(f"    header={diag.get('hasHeader')} adAnchors={diag.get('adAnchors')}")
            print(f"    section={diag.get('section')}")
            print(f"    parent={data.get('parentTag')}")
            for c in (diag.get("chains") or []):
                print(f"    chain {c}")
            for o in (diag.get("outline") or []):
                print(f"    child {o}")
            for h, n in (diag.get("hosts") or []):
                print(f"    host {h} x {n}")
            for j2, it2 in enumerate(items, 1):
                print(f"    item{j2}={(it2.get('raw') or '')[:80]}")
        ts = now_kst()
        for i, it in enumerate(items, start=1):
            company, ad_copy = parse_item(it)
            rows.append([ts, dev["name"], KEYWORD, i, company, ad_copy,
                         it.get("imageUrl", ""), it.get("raw", "")])
        print(f"  [{dev['name']}] 추출 모드={mode or '미검출'} · {len(rows)}건")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        browser.close()
    return rows, error


def add_missing_watch(rows, device_name):
    """중점 업체가 목록에 없으면 순위 0 / '미노출' 로 한 줄 남긴다."""
    ts = now_kst()
    blob = " ".join((r[4] or "") + " " + (r[7] or "") for r in rows)
    extra = []
    for name in WATCH:
        if name not in blob:
            extra.append([ts, device_name, KEYWORD, 0, name, "미노출", "", ""])
    return extra


def migrate_if_needed():
    """헤더가 바뀌었으면 기존 파일을 rankings_old.csv 로 밀어내고 새로 시작한다."""
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
        return
    try:
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            first = next(csv.reader(f), [])
    except Exception:
        return
    if [c.strip() for c in first] == HEADER:
        return
    if os.path.exists(OLD_CSV_PATH):
        os.remove(OLD_CSV_PATH)
    os.rename(CSV_PATH, OLD_CSV_PATH)
    print(f"[스키마 변경] 기존 {len(first)}칸 CSV → {OLD_CSV_PATH} 로 이동, 새 파일로 시작")


def append_csv(rows):
    new_file = not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        w.writerows(rows)


def main():
    migrate_if_needed()
    ts = now_kst()
    print(f"[{ts}] 수집 시작")

    with sync_playwright() as p:
        for dev in DEVICES:
            rows, error = scrape_device(p, dev)
            if error:
                append_csv([[now_kst(), dev["name"], KEYWORD, 0, "", f"수집실패: {error}", "", ""]])
                print(f"[{now_kst()}] {dev['name']} 수집 실패: {error}")
                continue
            if not rows:
                append_csv([[now_kst(), dev["name"], KEYWORD, 0, "",
                             "이 시각 파워링크 광고 없음(또는 영역 미검출)", "", ""]])
                print(f"[{now_kst()}] {dev['name']} 파워링크 0건 — "
                      f"{dev['debug_html']} 확인해 보정 필요")
                continue
            rows += add_missing_watch(rows, dev["name"])
            append_csv(rows)
            shown = [r for r in rows if r[3] != 0]
            print(f"[{now_kst()}] {dev['name']} 파워링크 {len(shown)}건 기록 완료")
            for r in shown:
                print(f"    {r[3]}위 | {r[4][:20]} | {r[5][:40]}")
            for r in rows:
                if r[3] == 0:
                    print(f"    -- {r[4]} 미노출")


if __name__ == "__main__":
    main()
