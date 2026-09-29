# 每日資金流向報告（台股／港股／美股／中概股）

每個交易日台北時間 **18:07** 自動執行（20:07 再補抓一次），產生一頁手機可看的報告，**不需 JavaScript**。

| 市場 | 內容 | 來源 |
|---|---|---|
| 台股 | 上市＋上櫃個股三大法人買賣超（金額排序前 20）、近 5 日累計＋連續買賣超天數、大盤 20 日趨勢 | 證交所、櫃買中心 |
| 港股 | 港股通南向淨買入（滬＋深）、南向十大成交股淨買賣、北向十大成交、20 日趨勢 | 港交所 |
| 美股 | 11 大類股 ETF、大盤主題 ETF、約 60 檔權值股的估算淨流入、CMF、MFI、量比 | Yahoo Finance |
| 中概股 | 中國 ETF（KWEB、FXI…）、約 28 檔中概 ADR | Yahoo Finance |

---

## 一次性設定（約 10 分鐘，手機也能做，電腦較方便）

1. **註冊／登入 GitHub**：<https://github.com>
2. **建立 repo**：右上「＋」→ New repository → 名稱填 `fund-flow` → 選 **Public** → Create。
   > 免費帳號的 GitHub Pages 只能用 Public repo。報告內容都是公開市場資料，沒有個人資訊。
3. **上傳檔案**：在新 repo 頁面點「uploading an existing file」，把解壓後資料夾內**所有檔案與資料夾**（含 `.github`）拖進去 → Commit changes。
   > 若 `.github` 資料夾沒被拖進去（有些系統會隱藏以 `.` 開頭的資料夾）：點 Add file → Create new file，檔名輸入 `.github/workflows/daily.yml`，把本包內同名檔案內容貼上即可。
4. **開放寫入權限**：Settings → Actions → General → 最下方 Workflow permissions 選 **Read and write permissions** → Save。
5. **第一次執行（含回補歷史）**：上方 Actions 分頁 →（若出現提示按「I understand… enable」）→ 左側「每日資金流向報告」→ Run workflow → backfill 填 `30` → Run。約 10–15 分鐘跑完（證交所有限速，需放慢）。
6. **開啟網頁**：Settings → Pages → Source 選 **Deploy from a branch** → Branch 選 `main`、資料夾選 `/docs` → Save。1–2 分鐘後網址會顯示在同頁上方：
   `https://你的帳號.github.io/fund-flow/`
   存成手機主畫面書籤即可每天看。

之後完全自動，不用再動。

## 日常

- **手動更新**：Actions → Run workflow（backfill 留 0）。
- **歷史報告**：頁面上方「歷史報告」。
- **原始資料**：`data/` 資料夾（CSV／JSON），可直接用 Excel 開啟。
- **執行失敗**：GitHub 會寄信通知；Actions 分頁點進紅色那次可看紀錄。常見原因是資料源暫時連不上，下次排程會自動補上。

## 自訂

打開 `fund_flow.py`，搜尋以下字典即可增刪追蹤標的（格式 `"代號": "中文名"`）：
- `US_STOCKS`：美股權值股
- `CN_ADR`、`CN_ETF`：中概股
- `US_SECTORS`、`US_BROAD`：類股／主題 ETF

## 數字怎麼讀

- **紅＝流入／買超，綠＝流出／賣超**（台灣慣例）。
- **台股**：上市大盤金額為證交所官方數字；個股金額＝買賣超股數 × 收盤價（估算）；上櫃大盤金額為個股加總估算。
- **港股**：南向＝內地資金買港股，港交所只公布十大成交股的買賣額，所以個股層級僅有這 10–20 檔。
- **美股／中概**：美國沒有官方每日資金流向，此處以量價估算：
  - 估算淨流入 ＝ CLV × 成交金額，CLV ＝ [(收−低)−(高−收)] ÷ (高−低)。收盤越接近當日高點，視為買盤越強。
  - CMF（20 日）> 0 偏流入；MFI（14 日）> 80 過熱、< 20 過冷；量比 ＞ 1.5 代表成交明顯放大。
- **北向資金**（外資買 A 股）自 2024 年起不再公布買賣方向，只提供成交熱度。

本報告僅供資訊整理，不構成投資建議。
