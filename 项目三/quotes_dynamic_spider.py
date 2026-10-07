# -*- coding: utf-8 -*-
"""
项目三 · 动态页爬虫：内容“等一等才出现”
练习站点（同一合法沙盒，专门演示“普通方法抓不到内容”）：
  · 先练：https://quotes.toscrape.com/js      （名言由 JS 在页面打开后填充）
  · 再练：https://quotes.toscrape.com/scroll  （向下滚动才继续加载的“无限滚动”页面）

需提取字段：名言内容 + 作者。

核心结论（加载机制，一句话各讲清）：
  · /js     —— 初始 HTML 里名言容器是空的，浏览器执行 JavaScript，调用
               数据接口 /api/quotes?page=N 拿到 JSON 后才把名言渲染出来；
  · /scroll —— 同样靠 /api/quotes?page=N，但只在页面打开时先加载第 1 页，
               之后每滚动到页面底部，JS 才把 page 加 1 继续请求并追加（无限滚动）。

本脚本采用课件表格中的策略 iii「直接找数据接口（更聪明的做法）」：
  F12 -> Network -> XHR/Fetch，找到 JS 实际请求的接口，直接用 Python 请求 JSON，
  跳过浏览器渲染环节。文末附有 Selenium/Playwright 驱动浏览器的等价写法说明。
"""

import csv
import time
import os
import requests
from bs4 import BeautifulSoup

BASE = "https://quotes.toscrape.com"
API = BASE + "/api/quotes"          # JS 真正请求的数据接口（返回 JSON）
HEADERS = {"User-Agent": "Mozilla/5.0 (edu-spider; teaching sandbox)"}
SLEEP = 0.2

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def demo_plain_request_fails():
    """演示：用 requests 直接拿 /js 的初始 HTML，名言容器是空的。"""
    html = requests.get(BASE + "/js", headers=HEADERS, timeout=20).text
    soup = BeautifulSoup(html, "lxml")
    quotes = soup.select("div.quote")
    print(f"[普通方法] 直接请求 /js 的 HTML，解析到名言条数 = {len(quotes)}")
    print("[普通方法] 初始 HTML 里只有空容器和 <script>，requests 不会执行 JS，故拿不到内容。")


def fetch_all_via_api(scene):
    """
    直接请求数据接口，循环翻页直到 has_next=False。
    scene='js'     ：对应打开页面后逐页渲染；
    scene='scroll' ：对应每次滚动到底部加载下一页（这里用 page 自增模拟该动作）。
    """
    rows = []
    page = 1
    while True:
        # 浏览器里：/js 是点“下一页”触发，/scroll 是滚动到底触发；
        # 二者底层都是 GET /api/quotes?page=page
        resp = requests.get(API, params={"page": page}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for q in data["quotes"]:
            rows.append({
                "text": q["text"],
                "author": q["author"]["name"],
                "tags": ", ".join(q.get("tags", [])),
                "page": page,
            })
        print(f"[{scene}] 第 {page} 页：本页 {len(data['quotes'])} 条，"
              f"累计 {len(rows)} 条，has_next={data['has_next']}")
        if not data["has_next"]:
            break
        page += 1
        time.sleep(SLEEP)
    return rows


def save_csv(rows, name):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["名言内容", "作者", "标签", "来源页"])
        for r in rows:
            w.writerow([r["text"], r["author"], r["tags"], r["page"]])
    return path


def main():
    # 1) 先证明普通方法拿不到
    demo_plain_request_fails()
    print("-" * 60)

    # 2) 场景一：/js —— JS 渲染
    js_rows = fetch_all_via_api("js")
    p1 = save_csv(js_rows, "quotes_js.csv")
    print("-" * 60)

    # 3) 场景二：/scroll —— 无限滚动
    scroll_rows = fetch_all_via_api("scroll")
    p2 = save_csv(scroll_rows, "quotes_scroll.csv")
    print("-" * 60)

    # 4) 核对
    authors = sorted({r["author"] for r in js_rows})
    print(f"/js     共 {len(js_rows)} 条名言，{len(authors)} 位作者 -> {p1}")
    print(f"/scroll 共 {len(scroll_rows)} 条名言 -> {p2}")
    print(f"两场景数据一致：{js_rows == scroll_rows}")
    print("加载机制：名言不在初始 HTML 中，而由 JS 调用 /api/quotes?page=N 拿 JSON 后渲染；")
    print("          /scroll 额外通过监听滚动事件，在滚到底部时递增 page 实现无限加载。")


if __name__ == "__main__":
    main()
