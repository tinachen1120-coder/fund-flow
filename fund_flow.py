# -*- coding: utf-8 -*-
"""
每日資金流向報告：台股／港股／美股／中概股
------------------------------------------------
資料來源（皆為免費公開資料）：
  台股上市：證交所 T86 三大法人買賣超、BFI82U 三大法人金額、STOCK_DAY_ALL 收盤價
  台股上櫃：櫃買中心 三大法人買賣明細、上櫃收盤行情
  港股：港交所 滬深港通每日成交統計（南向買入/賣出、十大成交股）
  美股、中概股：Yahoo Finance 日線（以 CLV × 成交金額 估算淨流入）

用法：
  python fund_flow.py                 # 抓最新資料並產生報告
  python fund_flow.py --backfill 20   # 回補過去 20 個日曆日的台股／港股歷史（首次建議執行）
  python fund_flow.py --demo          # 以假資料產生範例報告（測試版面用）

本報告僅供資訊整理，不構成投資建議。
"""
import argparse
import csv
import datetime as dt
import html
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, params=None, tries=3, pause=2.0, as_json=True):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json() if as_json else r.text
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                return None
        except Exception as e:  # noqa
            last = repr(e)
        time.sleep(pause * (i + 1))
    log(f"  ! 失敗 {url} {params or ''} -> {last}")
    return None


def num(x):
    """'1,234' / '-1,234.5' / '--' -> float or None"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"<[^>]+>", "", str(x)).replace(",", "").replace("+", "").strip()
    if s in ("", "--", "---", "X", "除權息", "除息", "除權"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fidx(fields, *keys):
    """找欄位索引：fields 中第一個「完全符合」否則「包含」keys 任一者"""
    for k in keys:
        if k in fields:
            return fields.index(k)
    for k in keys:
        for i, f in enumerate(fields):
            if k in f:
                return i
    return None


# ============================================================ 台股
TW_POLITE = 3.0  # 證交所限速：每次請求間隔秒數


def tw_twse(d: dt.date):
    """上市：個股三大法人（股數）+ 收盤價 + 大盤金額"""
    ds = d.strftime("%Y%m%d")
    j = get("https://www.twse.com.tw/rwd/zh/fund/T86",
            {"date": ds, "selectType": "ALLBUT0999", "response": "json"})
    time.sleep(TW_POLITE)
    if not j or j.get("stat") != "OK" or not j.get("data"):
        return None
    f = j["fields"]
    ic, iname = fidx(f, "證券代號"), fidx(f, "證券名稱")
    ifo = fidx(f, "外陸資買賣超股數(不含外資自營商)", "外資買賣超股數")
    itr = fidx(f, "投信買賣超股數")
    idl = fidx(f, "自營商買賣超股數")
    itot = fidx(f, "三大法人買賣超股數")
    rows = {}
    for r in j["data"]:
        code = str(r[ic]).strip()
        rows[code] = dict(code=code, name=str(r[iname]).strip(), mkt="上市",
                          foreign=num(r[ifo]) or 0, trust=num(r[itr]) or 0,
                          dealer=num(r[idl]) or 0, total=num(r[itot]) or 0)

    # 收盤價
    p = get("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL",
            {"date": ds, "response": "json"})
    time.sleep(TW_POLITE)
    if p and p.get("data"):
        pf = p["fields"]
        pc, pcl, pch = fidx(pf, "證券代號"), fidx(pf, "收盤價"), fidx(pf, "漲跌價差")
        for r in p["data"]:
            code = str(r[pc]).strip()
            if code in rows:
                c = num(r[pcl]); ch = num(r[pch]) if pch is not None else None
                rows[code]["close"] = c
                if c and ch is not None and c - ch:
                    rows[code]["chg"] = ch / (c - ch) * 100
    # 大盤金額（官方）
    b = get("https://www.twse.com.tw/rwd/zh/fund/BFI82U",
            {"type": "day", "dayDate": ds, "weekDate": ds, "monthDate": ds, "response": "json"})
    time.sleep(TW_POLITE)
    summ = {}
    if b and b.get("stat") == "OK":
        for r in b["data"]:
            k, v = r[0], num(r[3])
            if "外資及陸資" in k:
                summ["foreign"] = v
            elif k == "投信":
                summ["trust"] = v
            elif k.startswith("自營商"):
                summ["dealer"] = (summ.get("dealer") or 0) + (v or 0)
            elif k == "合計":
                summ["total"] = v
    return rows, summ


def tw_tpex(d: dt.date):
    """上櫃：個股三大法人 + 收盤價"""
    ds = d.strftime("%Y/%m/%d")
    j = get("https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade",
            {"type": "Daily", "sect": "EW", "date": ds, "id": "", "response": "json"})
    time.sleep(TW_POLITE)
    try:
        data = j["tables"][0]["data"]
    except Exception:
        return None
    if not data:
        return None
    rows = {}
    for r in data:
        if len(r) < 24:
            continue
        code = str(r[0]).strip()
        rows[code] = dict(code=code, name=str(r[1]).strip(), mkt="上櫃",
                          foreign=num(r[4]) or 0, trust=num(r[13]) or 0,
                          dealer=num(r[22]) or 0, total=num(r[23]) or 0)
    p = get("https://www.tpex.org.tw/www/zh-tw/afterTrading/otc",
            {"date": ds, "type": "EW", "id": "", "response": "json"})
    time.sleep(TW_POLITE)
    try:
        t = p["tables"][0]
        pf = t["fields"]
        pc, pcl, pch = fidx(pf, "代號"), fidx(pf, "收盤"), fidx(pf, "漲跌")
        for r in t["data"]:
            code = str(r[pc]).strip()
            if code in rows:
                c = num(r[pcl]); ch = num(r[pch]) if pch is not None else None
                rows[code]["close"] = c
                if c and ch is not None and c - ch:
                    rows[code]["chg"] = ch / (c - ch) * 100
    except Exception:
        log("  ! 上櫃收盤價無法取得，上櫃個股僅能以股數排序")
    return rows


TW_COLS = ["code", "name", "mkt", "close", "chg", "foreign", "trust", "dealer", "total",
           "foreign_amt", "trust_amt", "dealer_amt", "total_amt"]


def tw_fetch_day(d):
    a = tw_twse(d)
    if a is None:
        return None
    rows, summ = a
    otc = tw_tpex(d) or {}
    rows.update(otc)
    for r in rows.values():
        c = r.get("close")
        for k in ("foreign", "trust", "dealer", "total"):
            r[k + "_amt"] = r[k] * c if c else None
    # 上櫃金額以股數×收盤價估算
    o = [r for r in rows.values() if r["mkt"] == "上櫃"]
    summ["otc_foreign"] = sum(r["foreign_amt"] or 0 for r in o)
    summ["otc_trust"] = sum(r["trust_amt"] or 0 for r in o)
    summ["otc_dealer"] = sum(r["dealer_amt"] or 0 for r in o)
    summ["otc_n"] = len(o)
    return list(rows.values()), summ


def save_tw(d, rows, summ):
    (DATA / "tw").mkdir(parents=True, exist_ok=True)
    with open(DATA / "tw" / f"{d:%Y%m%d}.csv", "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, TW_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in TW_COLS})
    upsert(DATA / "tw_summary.csv", f"{d:%Y-%m-%d}",
           ["foreign", "trust", "dealer", "total", "otc_foreign", "otc_trust", "otc_dealer"], summ)


def load_tw(ds):
    out = []
    with open(DATA / "tw" / f"{ds}.csv", encoding="utf-8") as fp:
        for r in csv.DictReader(fp):
            for k in TW_COLS[3:]:
                r[k] = num(r[k])
            out.append(r)
    return out


# ============================================================ 港股通
HK_BASE = "https://www.hkex.com.hk/{lang}/csm/DailyStat/data_tab_daily_{ds}{s}.js"


def hk_parse(txt):
    m = re.search(r"=\s*(\[.*\])\s*;?\s*$", txt.strip(), re.S)
    return json.loads(m.group(1)) if m else None


def hk_fetch_day(d):
    ds = d.strftime("%Y%m%d")
    txt = get(HK_BASE.format(lang="eng", ds=ds, s="e"), as_json=False, tries=2)
    if not txt:
        return None
    tabs = hk_parse(txt)
    if not tabs:
        return None
    # 中文名稱（依相同位置對應）
    zh = {}
    ztxt = get(HK_BASE.format(lang="chi", ds=ds, s="c"), as_json=False, tries=2)
    ztabs = hk_parse(ztxt) if ztxt else None
    res = {"sb_buy": 0.0, "sb_sell": 0.0, "stocks": {}, "nb": [], "channels": {}}
    ok = False
    for i, t in enumerate(tabs):
        mk = t.get("market", "")
        if not t.get("tradingDay"):
            continue
        cont = t.get("content", [])
        if len(cont) < 2:
            continue
        top = cont[1]["table"]["tr"]
        ztop = None
        try:
            ztop = ztabs[i]["content"][1]["table"]["tr"]
        except Exception:
            pass
        if "Southbound" in mk:
            tds = cont[0]["table"]["tr"][0]["td"]
            flat = [x[0] if isinstance(x, list) else x for x in tds]
            if len(flat) < 3:  # 某些版本每格一列
                flat = [row["td"][0][0] for row in cont[0]["table"]["tr"]]
            buy, sell = num(flat[1]) or 0, num(flat[2]) or 0
            res["sb_buy"] += buy; res["sb_sell"] += sell
            res["channels"][mk] = buy - sell
            ok = True
            for j, row in enumerate(top):
                v = row["td"][0]
                code = v[1]
                name = v[2]
                if ztop:
                    try:
                        name = ztop[j]["td"][0][2]
                    except Exception:
                        pass
                b, s_ = num(v[3]) or 0, num(v[4]) or 0
                st = res["stocks"].setdefault(code, {"code": code, "name": name, "buy": 0, "sell": 0,
                                                     "ch": []})
                st["buy"] += b; st["sell"] += s_; st["ch"].append("滬" if "SSE" in mk else "深")
        elif "Northbound" in mk:
            for j, row in enumerate(top):
                v = row["td"][0]
                name = v[2]
                if ztop:
                    try:
                        name = ztop[j]["td"][0][2]
                    except Exception:
                        pass
                res["nb"].append({"code": v[1], "name": name, "turnover": num(v[3]) or 0,
                                  "ch": "滬股通" if "SSE" in mk else "深股通"})
    if not ok:
        return None
    for st in res["stocks"].values():
        st["net"] = st["buy"] - st["sell"]
    res["sb_net"] = res["sb_buy"] - res["sb_sell"]
    return res


def save_hk(d, res):
    (DATA / "hk").mkdir(parents=True, exist_ok=True)
    with open(DATA / "hk" / f"{d:%Y%m%d}.json", "w", encoding="utf-8") as fp:
        json.dump(res, fp, ensure_ascii=False)
    upsert(DATA / "hk_summary.csv", f"{d:%Y-%m-%d}", ["sb_buy", "sb_sell", "sb_net"], res)


# ============================================================ 美股／中概（Yahoo）
US_SECTORS = {
    "XLK": "科技", "XLC": "通訊服務", "XLY": "非必需消費", "XLF": "金融", "XLV": "醫療保健",
    "XLI": "工業", "XLE": "能源", "XLB": "原物料", "XLP": "必需消費", "XLU": "公用事業",
    "XLRE": "不動產",
}
US_BROAD = {"SPY": "標普500", "QQQ": "那斯達克100", "DIA": "道瓊", "IWM": "羅素2000",
            "SMH": "半導體", "TLT": "20年美債", "GLD": "黃金", "HYG": "高收益債"}
US_STOCKS = {
    "AAPL": "蘋果", "MSFT": "微軟", "NVDA": "輝達", "AMZN": "亞馬遜", "GOOGL": "Alphabet",
    "META": "Meta", "AVGO": "博通", "TSLA": "特斯拉", "BRK-B": "波克夏B", "JPM": "摩根大通",
    "LLY": "禮來", "V": "Visa", "UNH": "聯合健康", "XOM": "埃克森美孚", "MA": "萬事達",
    "COST": "好市多", "HD": "家得寶", "PG": "寶僑", "JNJ": "嬌生", "NFLX": "Netflix",
    "ORCL": "甲骨文", "ABBV": "艾伯維", "BAC": "美國銀行", "CRM": "Salesforce", "AMD": "超微",
    "KO": "可口可樂", "CVX": "雪佛龍", "WMT": "沃爾瑪", "MRK": "默克", "PEP": "百事",
    "ADBE": "Adobe", "CSCO": "思科", "MCD": "麥當勞", "WFC": "富國銀行", "IBM": "IBM",
    "GE": "奇異航太", "QCOM": "高通", "TXN": "德州儀器", "INTU": "Intuit", "DIS": "迪士尼",
    "CAT": "開拓重工", "AMAT": "應用材料", "PLTR": "Palantir", "MU": "美光", "INTC": "英特爾",
    "UBER": "Uber", "NOW": "ServiceNow", "GS": "高盛", "BA": "波音", "TSM": "台積電ADR",
    "ARM": "安謀", "LRCX": "科林研發", "KLAC": "科磊", "ANET": "Arista", "COIN": "Coinbase",
    "MSTR": "Strategy", "SMCI": "美超微", "DELL": "戴爾", "ASML": "艾司摩爾",
}
CN_ETF = {"KWEB": "中概網路", "FXI": "中國大型股", "MCHI": "MSCI中國", "CQQQ": "中國科技",
          "ASHR": "滬深300", "YINN": "中國3倍多"}
CN_ADR = {
    "BABA": "阿里巴巴", "PDD": "拼多多", "JD": "京東", "BIDU": "百度", "NTES": "網易",
    "TCOM": "攜程", "BILI": "嗶哩嗶哩", "NIO": "蔚來", "XPEV": "小鵬", "LI": "理想",
    "TME": "騰訊音樂", "ZTO": "中通快遞", "BEKE": "貝殼", "YUMC": "百勝中國", "VIPS": "唯品會",
    "HTHT": "華住", "FUTU": "富途", "TAL": "好未來", "EDU": "新東方", "QFIN": "奇富科技",
    "IQ": "愛奇藝", "MNSO": "名創優品", "BZ": "看準科技", "WB": "微博", "GDS": "萬國數據",
    "LU": "陸金所", "ATHM": "汽車之家", "ZK": "極氪",
}


def yahoo(t):
    for host in ("query1", "query2"):
        j = get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{t}",
                {"range": "3mo", "interval": "1d"}, tries=2, pause=1.5)
        try:
            r = j["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            ts = r["timestamp"]
            bars = []
            for i in range(len(ts)):
                o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
                if None in (h, l, c, v):
                    continue
                bars.append((dt.datetime.fromtimestamp(ts[i], ZoneInfo("America/New_York")).date(),
                             h, l, c, v))
            if len(bars) >= 22:
                return bars
        except Exception:
            pass
    return None


def flow_metrics(bars):
    """估算淨流入 = CLV × 成交金額；CLV=((C-L)-(H-C))/(H-L)。另計 CMF20、MFI14、量比。"""
    def clv(h, l, c):
        return ((c - l) - (h - c)) / (h - l) if h > l else 0.0
    dv = [(h + l + c) / 3 * v for _, h, l, c, v in bars]
    fl = [clv(h, l, c) * x for (_, h, l, c, v), x in zip(bars, dv)]
    closes = [b[3] for b in bars]
    vols = [b[4] for b in bars]
    cmf = sum(clv(h, l, c) * v for _, h, l, c, v in bars[-20:]) / max(sum(vols[-20:]), 1)
    # MFI14
    tp = [(h + l + c) / 3 for _, h, l, c, _ in bars]
    pos = neg = 0.0
    for i in range(len(bars) - 14, len(bars)):
        if tp[i] > tp[i - 1]:
            pos += tp[i] * vols[i]
        elif tp[i] < tp[i - 1]:
            neg += tp[i] * vols[i]
    mfi = 100 if neg == 0 else 100 - 100 / (1 + pos / neg)
    avgdv = sum(dv[-21:-1]) / 20
    return dict(date=bars[-1][0], close=closes[-1],
                chg=(closes[-1] / closes[-2] - 1) * 100,
                chg5=(closes[-1] / closes[-6] - 1) * 100,
                dv=dv[-1], f1=fl[-1], f5=sum(fl[-5:]), f20=sum(fl[-20:]),
                cmf=cmf, mfi=mfi, vr=dv[-1] / avgdv if avgdv else None)


def us_fetch(groups):
    out = {}
    for g, mp in groups.items():
        out[g] = []
        for t, n in mp.items():
            bars = yahoo(t)
            time.sleep(0.4)
            if not bars:
                log(f"  ! Yahoo 無資料 {t}")
                continue
            m = flow_metrics(bars)
            m.update(t=t, name=n)
            out[g].append(m)
    return out


# ============================================================ 共用
def upsert(path, key, cols, d):
    rows = {}
    if path.exists():
        with open(path, encoding="utf-8") as fp:
            for r in csv.DictReader(fp):
                rows[r["date"]] = r
    rows[key] = {"date": key, **{c: ("" if d.get(c) is None else d.get(c)) for c in cols}}
    with open(path, "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, ["date"] + cols)
        w.writeheader()
        for k in sorted(rows):
            w.writerow(rows[k])


def read_summary(path, n=20):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fp:
        rows = list(csv.DictReader(fp))
    for r in rows:
        for k in r:
            if k != "date":
                r[k] = num(r[k])
    return rows[-n:]


def latest_trading(fetch, save, today, back=7):
    for i in range(back):
        d = today - dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        log(f"  嘗試 {d}")
        r = fetch(d)
        if r:
            save(d, *r) if isinstance(r, tuple) else save(d, r)
            return d, r
    return None, None


# ============================================================ HTML
RED, GREEN = "#C8102E", "#0E8A4F"


def e(s):
    return html.escape(str(s))


def fmt_yi(v, dig=1):  # 元 -> 億
    return "—" if v is None else f"{v / 1e8:+,.{dig}f}"


def fmt_m(v, dig=1):  # -> 百萬
    return "—" if v is None else f"{v / 1e6:+,.{dig}f}"


def col(v):
    return RED if (v or 0) > 0 else GREEN if (v or 0) < 0 else "#6B7280"


def pct(v, dig=2):
    return "—" if v is None else f'<span style="color:{col(v)}">{v:+.{dig}f}%</span>'


def bar(v, vmax):
    w = 0 if not vmax or v is None else min(100, abs(v) / vmax * 100)
    return f'<div class="bar"><i style="width:{w:.1f}%;background:{col(v)}"></i></div>'


def dbar(v, vmax):
    """中心零軸雙向條"""
    w = 0 if not vmax or v is None else min(50, abs(v) / vmax * 50)
    side = "left:50%" if (v or 0) >= 0 else f"left:{50 - w:.1f}%"
    return (f'<div class="dbar"><b></b><i style="{side};width:{w:.1f}%;'
            f'background:{col(v)}"></i></div>')


def rank_table(rows, key, unit_fn, title, sub=None, n=20, extra=None):
    rows = [r for r in rows if r.get(key) is not None]
    if not rows:
        return f'<p class="muted">{e(title)}：無資料</p>'
    vmax = max(abs(r[key]) for r in rows) or 1
    h = [f'<h4>{e(title)}</h4><table class="rk"><tbody>']
    for i, r in enumerate(rows[:n], 1):
        ex = extra(r) if extra else ""
        h.append(f'<tr><td class="n">{i}</td><td class="nm"><b>{e(r.get("name",""))}</b>'
                 f'<small>{e(r.get("code") or r.get("t",""))}{ex}</small>{bar(r[key], vmax)}</td>'
                 f'<td class="v" style="color:{col(r[key])}">{unit_fn(r[key])}</td></tr>')
    h.append("</tbody></table>")
    if sub:
        h.append(f'<p class="note">{sub}</p>')
    return "".join(h)


def trend(rows, key, fmt, label):
    rows = [r for r in rows if r.get(key) is not None]
    if not rows:
        return ""
    vmax = max(abs(r[key]) for r in rows) or 1
    h = [f'<h4>{e(label)}</h4><table class="tr"><tbody>']
    for r in reversed(rows):
        h.append(f'<tr><td class="d">{r["date"][5:]}</td><td>{dbar(r[key], vmax)}</td>'
                 f'<td class="v" style="color:{col(r[key])}">{fmt(r[key])}</td></tr>')
    h.append("</tbody></table>")
    return "".join(h)


def card(label, v, fmt, sub=""):
    return (f'<div class="card"><span>{e(label)}</span><strong style="color:{col(v)}">'
            f'{fmt(v)}</strong><small>{sub}</small></div>')


def tw_section(twd, rows, summ_hist):
    if not rows:
        return '<p class="muted">台股資料尚未公布或休市。</p>', None
    today_s = next((r for r in reversed(summ_hist) if r["date"] == f"{twd:%Y-%m-%d}"), {})
    h = []
    h.append('<div class="cards">')
    h.append(card("外資（上市）", today_s.get("foreign"), lambda v: fmt_yi(v) + " 億"))
    h.append(card("投信（上市）", today_s.get("trust"), lambda v: fmt_yi(v) + " 億"))
    h.append(card("自營商（上市）", today_s.get("dealer"), lambda v: fmt_yi(v) + " 億"))
    h.append(card("外資（上櫃）", today_s.get("otc_foreign"), lambda v: fmt_yi(v) + " 億", "估算"))
    h.append(card("投信（上櫃）", today_s.get("otc_trust"), lambda v: fmt_yi(v) + " 億", "估算"))
    h.append(card("三大法人合計（上市）", today_s.get("total"), lambda v: fmt_yi(v) + " 億"))
    h.append("</div>")

    def ex(r):
        c = r.get("chg")
        if not r.get("close"):
            return f' · {r["mkt"]}'
        return f' · {r["mkt"]} · {r["close"]:,.2f} ' + (pct(c) if c is not None else "")
    yi = lambda v: f"{v / 1e8:+,.2f} 億"
    for key, nm in (("foreign_amt", "外資"), ("trust_amt", "投信"), ("dealer_amt", "自營商"),
                    ("total_amt", "三大法人合計")):
        buy = sorted([r for r in rows if (r.get(key) or 0) > 0], key=lambda r: -r[key])
        sell = sorted([r for r in rows if (r.get(key) or 0) < 0], key=lambda r: r[key])
        opened = " open" if nm in ("外資", "投信") else ""
        h.append(f'<details{opened}><summary>{nm} 買超／賣超 前 20 名（金額）</summary>'
                 f'<div class="two">{rank_table(buy, key, yi, nm + "買超", extra=ex)}'
                 f'{rank_table(sell, key, yi, nm + "賣超", extra=ex)}</div></details>')
    return "".join(h), today_s


def tw_streaks(files, n=5):
    """近 n 日外資／投信累計與連續買賣超天數"""
    hist = [load_tw(f) for f in files[-max(n, 10):]]
    if not hist:
        return ""
    by = {}
    for day in hist:
        for r in day:
            by.setdefault(r["code"], []).append(r)
    last_codes = {r["code"] for r in hist[-1]}
    out = []
    for key, nm in (("foreign", "外資"), ("trust", "投信")):
        stats = []
        for code, lst in by.items():
            if code not in last_codes:
                continue
            seq = [x.get(key) or 0 for x in lst]
            streak = 0
            sgn = 1 if seq[-1] > 0 else -1 if seq[-1] < 0 else 0
            if sgn:
                for v in reversed(seq):
                    if v * sgn > 0:
                        streak += 1
                    else:
                        break
            amt = sum((x.get(key + "_amt") or 0) for x in lst[-n:])
            stats.append(dict(code=code, name=lst[-1]["name"], mkt=lst[-1]["mkt"],
                              amt=amt, streak=streak * sgn))
        buy = sorted([s for s in stats if s["amt"] > 0], key=lambda s: -s["amt"])
        sell = sorted([s for s in stats if s["amt"] < 0], key=lambda s: s["amt"])
        exf = lambda s: (f' · {s["mkt"]} · 連{"買" if s["streak"] > 0 else "賣"} '
                         f'{abs(s["streak"])} 天' if s["streak"] else f' · {s["mkt"]}')
        yi = lambda v: f"{v / 1e8:+,.2f} 億"
        out.append(f'<details><summary>{nm} 近 {min(n, len(hist))} 日累計 前 15 名＋連續天數</summary>'
                   f'<div class="two">{rank_table(buy, "amt", yi, nm + "累計買超", n=15, extra=exf)}'
                   f'{rank_table(sell, "amt", yi, nm + "累計賣超", n=15, extra=exf)}</div></details>')
    return "".join(out)


def hk_section(hkd, res, hist):
    if not res:
        return '<p class="muted">港股通資料尚未公布或休市。</p>'
    h = ['<div class="cards">',
         card("南向淨買入", res["sb_net"] * 1e6, lambda v: f"{v / 1e8:+,.1f} 億港元"),
         card("南向買入", res["sb_buy"] * 1e6, lambda v: f"{v / 1e8:,.1f} 億"),
         card("南向賣出", -res["sb_sell"] * 1e6, lambda v: f"{-v / 1e8:,.1f} 億")]
    for k, v in res["channels"].items():
        nm = "滬港通南向" if "SSE" in k else "深港通南向"
        h.append(card(nm, v * 1e6, lambda x: f"{x / 1e8:+,.1f} 億"))
    h.append("</div>")
    st = sorted(res["stocks"].values(), key=lambda s: -s["net"])
    hk = lambda v: f"{v / 1e8:+,.2f} 億"
    exf = lambda s: f' · 買 {s["buy"] / 1e8:,.1f} / 賣 {s["sell"] / 1e8:,.1f} 億 · {"＋".join(s["ch"])}'
    h.append('<details open><summary>南向十大成交股 淨買入排序（港元）</summary>')
    h.append(rank_table(st, "net", hk, "南向個股淨買賣", n=20, extra=exf,
                        sub="港交所每日僅公布滬／深港通各十大成交股的買入與賣出額，滬深重複者已合併。"))
    h.append("</details>")
    nb = sorted(res["nb"], key=lambda s: -s["turnover"])
    if nb:
        rmb = lambda v: f"{v / 1e8:,.1f} 億"
        h.append('<details><summary>北向十大成交股（外資買賣 A 股熱度，人民幣）</summary>')
        h.append(rank_table(nb, "turnover", rmb, "北向成交額", n=20,
                            extra=lambda s: " · " + s["ch"],
                            sub="北向資金自 2024 年起不再公布買賣方向，僅能看成交熱度。"))
        h.append("</details>")
    h.append(trend(hist, "sb_net", lambda v: f"{v / 100:+,.1f} 億", "南向淨買入 近 20 日（港元）"))
    return "".join(h)


def us_block(items, title, top=15, show_all=False):
    if not items:
        return f'<p class="muted">{e(title)}：無資料</p>'
    usd = lambda v: f"{v / 1e6:+,.0f} M"
    exf = lambda r: (f' · ${r["close"]:,.2f} {pct(r["chg"])} · CMF {r["cmf"]:+.2f} · MFI {r["mfi"]:.0f}'
                     + (f' · 量比 {r["vr"]:.1f}' if r.get("vr") else ""))
    if show_all:
        s = sorted(items, key=lambda r: -r["f1"])
        return (rank_table(s, "f1", usd, title + "｜今日估算淨流入（美元）", n=len(s), extra=exf)
                + rank_table(sorted(items, key=lambda r: -r["f5"]), "f5", usd,
                             title + "｜近 5 日累計", n=len(s), extra=exf))
    buy = sorted([r for r in items if r["f1"] > 0], key=lambda r: -r["f1"])
    sell = sorted([r for r in items if r["f1"] < 0], key=lambda r: r["f1"])
    b5 = sorted(items, key=lambda r: -r["f5"])
    return (f'<div class="two">{rank_table(buy, "f1", usd, title + "｜今日流入", n=top, extra=exf)}'
            f'{rank_table(sell, "f1", usd, title + "｜今日流出", n=top, extra=exf)}</div>'
            f'<details><summary>{e(title)} 近 5 日累計排序</summary>'
            f'{rank_table(b5, "f5", usd, "近 5 日累計估算淨流", n=len(b5), extra=exf)}</details>')


CSS = """
:root{--bg:#F7F5F0;--card:#fff;--ink:#1E2A44;--mut:#6B7280;--line:#E5E1D8;--acc:#2E75B6}
@media (prefers-color-scheme:dark){:root{--bg:#12161F;--card:#1B2130;--ink:#E6E9EF;--mut:#98A1B3;--line:#2A3243;--acc:#6FA8DC}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
header h1{font-size:22px;margin:4px 0}header p{margin:0;color:var(--mut);font-size:13px}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:2}
nav a{padding:6px 12px;border-radius:999px;background:var(--card);border:1px solid var(--line);
color:var(--ink);text-decoration:none;font-size:14px}
section{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:14px 0}
section>h2{margin:0 0 4px;font-size:19px}section>.dt{color:var(--mut);font-size:13px;margin-bottom:10px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:8px 0 12px}
.card{border:1px solid var(--line);border-radius:10px;padding:8px 10px;display:flex;flex-direction:column}
.card span{font-size:12px;color:var(--mut)}.card strong{font-size:18px;font-variant-numeric:tabular-nums}
.card small{font-size:11px;color:var(--mut)}
details{border-top:1px solid var(--line);padding:6px 0}summary{cursor:pointer;font-weight:600;padding:6px 0;color:var(--acc)}
h4{margin:10px 0 4px;font-size:14px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:720px){.two{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse}td{padding:5px 4px;border-bottom:1px solid var(--line);vertical-align:top}
.rk .n{width:22px;color:var(--mut);font-size:12px;text-align:right}.nm b{font-weight:600}
.nm small{display:block;color:var(--mut);font-size:11.5px}
.v{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;font-weight:600}
.bar{height:4px;background:var(--line);border-radius:2px;margin-top:4px;overflow:hidden}.bar i{display:block;height:100%}
.tr td:nth-child(2){width:100%}.tr .v{width:1%}
.tr .d{width:48px;white-space:nowrap;color:var(--mut);font-size:12.5px}.tr td{border:0;padding:2px 4px;vertical-align:middle}
.dbar{position:relative;height:12px}.dbar b{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--mut)}
.dbar i{position:absolute;top:2px;bottom:2px;border-radius:2px}
.muted,.note{color:var(--mut);font-size:12.5px}
footer{color:var(--mut);font-size:12px;margin:24px 0}
.demo{background:#FFF4CC;color:#7A5B00;border:1px solid #E8C860;border-radius:10px;padding:10px;margin:10px 0;font-weight:600}
"""


def render(ctx):
    now = dt.datetime.now(TZ)
    H = [f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
         f'<meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>每日資金流向 {ctx["title_date"]}</title><style>{CSS}</style></head><body><div class="wrap">']
    H.append(f'<header><h1>每日資金流向</h1><p>產生時間 {now:%Y-%m-%d %H:%M}（台北）'
             f'｜紅色＝流入／買超，綠色＝流出／賣超</p></header>')
    if ctx.get("demo"):
        H.append('<div class="demo">⚠ 範例畫面：以下數字全為隨機假資料，僅供檢視版面。</div>')
    H.append('<nav><a href="#ov">總覽</a><a href="#tw">台股</a><a href="#hk">港股通</a>'
             '<a href="#us">美股</a><a href="#cn">中概股</a><a href="archive/">歷史報告</a></nav>')

    # 總覽
    ts = ctx.get("tw_today") or {}
    hk = ctx.get("hk")
    us = ctx.get("us") or {}
    sec = sorted(us.get("sector", []), key=lambda r: -r["f1"])
    cn = us.get("cn_adr", []) + us.get("cn_etf", [])
    H.append('<section id="ov"><h2>四市場總覽</h2><div class="cards">')
    H.append(card(f'台股外資 {ctx.get("tw_date") or ""}', ts.get("foreign"), lambda v: fmt_yi(v) + " 億"))
    H.append(card("台股投信", ts.get("trust"), lambda v: fmt_yi(v) + " 億"))
    if hk:
        H.append(card(f'南向資金 {ctx.get("hk_date") or ""}', hk["sb_net"] * 1e6,
                      lambda v: f"{v / 1e8:+,.1f} 億港元"))
    if sec:
        H.append(card(f"美股最強類股", sec[0]["f1"], lambda v: f"{v / 1e6:+,.0f} M",
                      f'{sec[0]["t"]} {sec[0]["name"]}'))
        H.append(card(f"美股最弱類股", sec[-1]["f1"], lambda v: f"{v / 1e6:+,.0f} M",
                      f'{sec[-1]["t"]} {sec[-1]["name"]}'))
    if cn:
        H.append(card("中概 ADR+ETF 合計", sum(r["f1"] for r in cn), lambda v: f"{v / 1e6:+,.0f} M",
                      "估算淨流入（美元）"))
    H.append("</div>")
    H.append(trend(ctx.get("tw_hist", []), "foreign", lambda v: fmt_yi(v, 0) + " 億",
                   "台股外資（上市）近 20 日"))
    H.append("</section>")

    # 台股
    H.append(f'<section id="tw"><h2>台股｜三大法人</h2><div class="dt">資料日 {ctx.get("tw_date") or "—"}'
             f'｜上市金額為證交所官方數字；個股金額＝買賣超股數×收盤價（估算）</div>')
    H.append(ctx.get("tw_html", ""))
    H.append(ctx.get("tw_streak_html", ""))
    H.append(trend(ctx.get("tw_hist", []), "trust", lambda v: fmt_yi(v, 0) + " 億", "投信（上市）近 20 日"))
    H.append("</section>")

    # 港股
    H.append(f'<section id="hk"><h2>港股｜港股通南向資金</h2><div class="dt">資料日 {ctx.get("hk_date") or "—"}'
             f'｜來源：港交所滬深港通每日統計</div>{ctx.get("hk_html", "")}</section>')

    # 美股
    ud = (us.get("sector") or [{}])[0].get("date", "—") if us.get("sector") else "—"
    H.append(f'<section id="us"><h2>美股｜估算資金流向</h2><div class="dt">資料日 {ud}（美東）'
             f'｜美股無官方資金流向數據，以量價估算</div>')
    H.append(us_block(us.get("sector", []), "11 大類股 ETF", show_all=True))
    H.append('<details><summary>大盤／主題 ETF</summary>' + us_block(us.get("broad", []), "大盤主題", show_all=True)
             + "</details>")
    H.append(us_block(us.get("stock", []), "大型權值股"))
    H.append("</section>")

    # 中概
    H.append('<section id="cn"><h2>中概股｜ADR 與中國 ETF</h2><div class="dt">美國掛牌中概股（港股中概見港股通南向）</div>')
    H.append(us_block(us.get("cn_etf", []), "中國相關 ETF", show_all=True))
    H.append(us_block(us.get("cn_adr", []), "中概 ADR", top=12))
    H.append("</section>")

    H.append('<footer><b>方法說明</b><br>'
             '・台股：證交所／櫃買中心盤後三大法人買賣超；外資＝外陸資（不含外資自營商）。<br>'
             '・港股：港交所公布的南向（內地資金買港股）買入、賣出成交額；北向僅有成交額。<br>'
             '・美股／中概：估算淨流入＝CLV×成交金額，CLV＝[(收−低)−(高−收)]/(高−低)，收在當日高點附近視為資金流入。'
             'CMF＝20 日 Chaikin 資金流量（>0 偏流入）；MFI＝14 日資金流量指標（>80 過熱、<20 過冷）；量比＝今日成交金額/前 20 日均值。'
             '此為量價推估，非實際申購贖回資料。<br>'
             '本報告僅供資訊整理，不構成投資建議。</footer></div></body></html>')
    return "".join(H)


def write_archive_index():
    arch = DOCS / "archive"
    files = sorted([p.name for p in arch.glob("20*.html")], reverse=True)
    li = "".join(f'<li><a href="{f}">{f[:-5]}</a></li>' for f in files)
    (arch / "index.html").write_text(
        f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>歷史報告</title>'
        f'<style>{CSS} li{{margin:6px 0}}</style></head><body><div class="wrap">'
        f'<h1>歷史報告</h1><p><a href="../">← 回最新報告</a></p><ul>{li}</ul></div></body></html>',
        encoding="utf-8")


# ============================================================ demo 假資料
def demo_ctx():
    random.seed(7)
    names = ["台積電", "鴻海", "聯發科", "廣達", "台達電", "日月光投控", "緯創", "富邦金", "國泰金",
             "長榮", "聯電", "中信金", "奇鋐", "緯穎", "世芯-KY", "智原", "創意", "元大台灣50",
             "華邦電", "南亞科", "力積電", "欣興", "臻鼎-KY", "技嘉", "英業達", "群聯", "信驊", "川湖"]
    rows = []
    for i, n in enumerate(names * 2):
        c = random.uniform(20, 1200)
        r = dict(code=str(2300 + i), name=n, mkt=random.choice(["上市", "上市", "上櫃"]), close=c,
                 chg=random.uniform(-5, 5))
        for k in ("foreign", "trust", "dealer"):
            r[k] = random.gauss(0, 3e6) * (1 if k == "foreign" else 0.4)
            r[k + "_amt"] = r[k] * c
        r["total"] = r["foreign"] + r["trust"] + r["dealer"]
        r["total_amt"] = r["total"] * c
        rows.append(r)
    hist = [dict(date=(dt.date(2026, 9, 24) - dt.timedelta(days=i)).isoformat(),
                 foreign=random.gauss(0, 2e10), trust=random.gauss(0, 5e9), dealer=random.gauss(0, 3e9),
                 total=random.gauss(0, 2e10), otc_foreign=random.gauss(0, 2e9), otc_trust=random.gauss(0, 1e9))
            for i in range(20)][::-1]
    hkhist = [dict(date=h["date"], sb_net=random.gauss(50, 60)) for h in hist]
    st = {}
    for code, n in [("00700", "騰訊控股"), ("09988", "阿里巴巴－Ｗ"), ("03690", "美團－Ｗ"),
                    ("01810", "小米集團－Ｗ"), ("00981", "中芯國際"), ("02513", "智譜"),
                    ("00939", "建設銀行"), ("01211", "比亞迪股份"), ("09992", "泡泡瑪特"), ("00388", "香港交易所")]:
        b, s_ = random.uniform(3e8, 3e9), random.uniform(3e8, 3e9)
        st[code] = dict(code=code, name=n, buy=b, sell=s_, net=b - s_, ch=["滬", "深"])
    hk = dict(sb_buy=28653.4, sb_sell=24230.0, sb_net=4423.4,
              channels={"SSE Southbound": 3200.1, "SZSE Southbound": 1223.3}, stocks=st,
              nb=[dict(code="603893", name="瑞芯微", turnover=1.8e9, ch="滬股通"),
                  dict(code="300750", name="寧德時代", turnover=1.6e9, ch="深股通")])

    def fake(mp):
        out = []
        for t, n in mp.items():
            d = random.uniform(1e8, 5e9)
            out.append(dict(t=t, name=n, date=dt.date(2026, 9, 24), close=random.uniform(10, 900),
                            chg=random.uniform(-4, 4), chg5=0, dv=d, f1=random.gauss(0, d * 0.3),
                            f5=random.gauss(0, d), f20=0, cmf=random.uniform(-.3, .3),
                            mfi=random.uniform(15, 85), vr=random.uniform(.5, 2.5)))
        return out
    us = dict(sector=fake(US_SECTORS), broad=fake(US_BROAD), stock=fake(US_STOCKS),
              cn_etf=fake(CN_ETF), cn_adr=fake(CN_ADR))
    tw_today = hist[-1]
    tw_html, _ = tw_section(dt.date(2026, 9, 24), rows, hist)
    return dict(demo=True, title_date="範例", tw_date="2026-09-24", tw_today=tw_today, tw_hist=hist,
                tw_html=tw_html, tw_streak_html="", hk=hk, hk_date="2026-09-24",
                hk_html=hk_section(None, hk, hkhist), us=us)


# ============================================================ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, help="回補過去 N 個日曆日的台股／港股")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--skip-us", action="store_true")
    a = ap.parse_args()

    if a.demo:
        DOCS.mkdir(exist_ok=True)
        (DOCS / "demo.html").write_text(render(demo_ctx()), encoding="utf-8")
        log("已產生 docs/demo.html")
        return

    today = dt.datetime.now(TZ).date()
    if a.backfill:
        for i in range(a.backfill, 0, -1):
            d = today - dt.timedelta(days=i)
            if d.weekday() >= 5:
                continue
            if not (DATA / "tw" / f"{d:%Y%m%d}.csv").exists():
                log(f"回補台股 {d}")
                r = tw_fetch_day(d)
                if r:
                    save_tw(d, *r)
            if not (DATA / "hk" / f"{d:%Y%m%d}.json").exists():
                log(f"回補港股通 {d}")
                r = hk_fetch_day(d)
                if r:
                    save_hk(d, r)

    log("台股…")
    twd, tw = latest_trading(tw_fetch_day, save_tw, today)
    log("港股通…")
    hkd, hk = latest_trading(hk_fetch_day, save_hk, today)
    us = {}
    if not a.skip_us:
        log("美股／中概…")
        us = us_fetch({"sector": US_SECTORS, "broad": US_BROAD, "stock": US_STOCKS,
                       "cn_etf": CN_ETF, "cn_adr": CN_ADR})

    tw_hist = read_summary(DATA / "tw_summary.csv")
    hk_hist = read_summary(DATA / "hk_summary.csv")
    tw_html, tw_today = tw_section(twd, tw[0] if tw else None, tw_hist)
    files = sorted(p.stem for p in (DATA / "tw").glob("*.csv")) if (DATA / "tw").exists() else []
    ctx = dict(title_date=f"{today:%Y-%m-%d}",
               tw_date=f"{twd:%Y-%m-%d}" if twd else None, tw_today=tw_today, tw_hist=tw_hist,
               tw_html=tw_html, tw_streak_html=tw_streaks(files) if len(files) >= 2 else "",
               hk=hk, hk_date=f"{hkd:%Y-%m-%d}" if hkd else None,
               hk_html=hk_section(hkd, hk, hk_hist), us=us)
    page = render(ctx)
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / "archive" / f"{today:%Y-%m-%d}.html").write_text(
        page.replace('href="archive/"', 'href="./"'), encoding="utf-8")
    write_archive_index()
    (DOCS / ".nojekyll").write_text("")
    log(f"完成：台股 {twd}｜港股通 {hkd}｜美股 {sum(len(v) for v in us.values())} 檔")
    if not (tw or hk or us):
        sys.exit(1)


if __name__ == "__main__":
    main()
