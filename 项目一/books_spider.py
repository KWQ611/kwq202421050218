# -*- coding: utf-8 -*-
"""
项目一 · 静态页爬虫：把整站图书整理成表格（断点续传版）
练习站点：https://books.toscrape.com （爬虫教学沙盒，允许自由抓取）

覆盖课件关键概念：
  1. 按“位置 + 属性”从网页结构中提取字段；
  2. 识别 URL 翻页规律后用循环遍历全部页面；
  3. 清理符号、空格；
  4. 不遗漏最后一页，用总条数核对完整性。
提取字段：书名、价格、评分、库存、详情页链接。

本版增强：列表数据先落盘，详情库存增量保存，网络波动后重跑可从断点继续，
失败的详情页最后统一补爬，避免中途异常前功尽弃。
"""

import re
import time
import csv
import json
import os
import requests
from bs4 import BeautifulSoup

BASE = "https://books.toscrape.com"
LIST_URL = BASE + "/catalogue/page-{}.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (edu-spider; teaching sandbox)"}
SLEEP = 0.1

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
LIST_CACHE = os.path.join(OUT_DIR, "books_list.json")
STOCK_CACHE = os.path.join(OUT_DIR, "books_stock.json")
CSV_PATH = os.path.join(OUT_DIR, "books.csv")

RATING_MAP = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch(url, retries=5, timeout=30):
    """带指数退避重试的请求；全部失败则抛出最后一次异常。"""
    last = None
    for i in range(retries):
        try:
            resp = SESSION.get(url, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"      # 站点 UTF-8，避免 “Â£” 乱码
            return resp.text
        except requests.RequestException as e:
            last = e
            time.sleep(min(2 ** (i + 1), 30))
    raise last


def try_fetch_or_none(url):
    """详情页用：失败返回 None，不中断整体，交给后续补爬。"""
    try:
        return fetch(url)
    except requests.RequestException:
        return None


def parse_list_page(html):
    soup = BeautifulSoup(html, "lxml")
    books = []
    for art in soup.select("article.product_pod"):
        a = art.select_one("h3 a")
        title = a["title"].strip()
        link = BASE + "/catalogue/" + a["href"].replace("../", "")
        price_text = art.select_one("p.price_color").get_text(strip=True)
        price = float(price_text.replace("£", "").strip())
        classes = art.select_one("p.star-rating")["class"]
        word = [c for c in classes if c != "star-rating"][0]
        rating = RATING_MAP[word]
        in_stock = art.select_one("p.instock.availability").get_text(strip=True)
        books.append({"title": title, "price": price, "rating": rating,
                      "stock_flag": in_stock, "link": link})
    return books


def parse_detail_stock(html):
    soup = BeautifulSoup(html, "lxml")
    avail = soup.select_one("p.instock.availability").get_text(" ", strip=True)
    m = re.search(r"(\d+)\s+available", avail)
    return int(m.group(1)) if m else 0


def load_or_crawl_list():
    if os.path.exists(LIST_CACHE):
        books = json.load(open(LIST_CACHE, encoding="utf-8"))
        print(f"载入列表缓存：{len(books)} 本")
        if len(books) >= 1000:
            return books
    books = []
    page = 1
    while True:
        try:
            html = fetch(LIST_URL.format(page))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                break
            raise
        batch = parse_list_page(html)
        if not batch:
            break
        books.extend(batch)
        json.dump(books, open(LIST_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"列表页 {page:>2}: 累计 {len(books)} 本")
        page += 1
        time.sleep(SLEEP)
    return books


def crawl_stocks(books):
    stocks = json.load(open(STOCK_CACHE, encoding="utf-8")) if os.path.exists(STOCK_CACHE) else {}
    todo = [b for b in books if stocks.get(b["link"]) is None]
    print(f"待取库存详情页：{len(todo)} / {len(books)}")
    done = 0
    for b in todo:
        html = try_fetch_or_none(b["link"])
        if html is not None:
            stocks[b["link"]] = parse_detail_stock(html)
            done += 1
        if done and done % 20 == 0:
            json.dump(stocks, open(STOCK_CACHE, "w", encoding="utf-8"))
            print(f"详情页进度：已取 {len(stocks)}/{len(books)}")
        time.sleep(SLEEP)
    json.dump(stocks, open(STOCK_CACHE, "w", encoding="utf-8"))
    return stocks


def main():
    books = load_or_crawl_list()

    # 详情页 + 失败补爬，最多 3 轮，直到全部取到
    for rnd in range(1, 4):
        stocks = crawl_stocks(books)
        missing = [b for b in books if stocks.get(b["link"]) is None]
        if not missing:
            break
        print(f"第 {rnd} 轮仍有 {len(missing)} 本未取到，等待后补爬 …")
        time.sleep(5)

    stocks = json.load(open(STOCK_CACHE, encoding="utf-8"))
    for b in books:
        b["stock"] = stocks.get(b["link"])

    fields = ["title", "price", "rating", "stock", "stock_flag", "link"]
    header = ["书名", "价格(£)", "评分", "库存数量", "库存状态", "详情页链接"]
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for b in books:
            w.writerow([b["title"], b["price"], b["rating"], b["stock"],
                        b["stock_flag"], b["link"]])

    missing = sum(1 for b in books if b["stock"] is None or not b["title"] or not b["link"])
    got_stock = [b for b in books if b["stock"] is not None]
    print("-" * 50)
    print(f"总条数: {len(books)} （站点总书量约 1000）")
    print(f"缺失/未取到的条数: {missing}")
    print(f"价格范围: £{min(b['price'] for b in books):.2f} ~ "
          f"£{max(b['price'] for b in books):.2f}")
    print(f"库存总数: {sum(b['stock'] for b in got_stock)}")
    print(f"已保存: {CSV_PATH}")


if __name__ == "__main__":
    main()
