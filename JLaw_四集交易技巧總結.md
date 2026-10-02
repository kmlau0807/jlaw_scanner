# J Law「終極股票教學課程」四集總索引（EP1–EP4）

> 講者：**J Law（JL）**，全職交易人、2024 全美投資大賽（$1M 真倉）冠軍（全年 +353.9%，破 1983 年記錄）
> 影片源（YouTube 播放清單 `PLlbPQImE4bdfGv6jcpxs7b99JKxePnNsq`）：
> - **EP1** 支持與阻力 — https://www.youtube.com/watch?v=vpKJoVtic_Y
> - **EP2** 趨勢線 — https://www.youtube.com/watch?v=zbb09zSKYts
> - **EP3** 平衡通道（講者 Jaylor）— https://www.youtube.com/watch?v=BM3jm780w4Q
> - **EP4** 移動平均線 × METS 奪冠實戰 — https://www.youtube.com/watch?v=Q4Ersajf9qg
> 各集詳細重點：見同資料夾 `youtube_*/summary.md`（已轉錄 + 校正同音錯字）。

---

## 一、四集一句話總結

| EP | 主題 | 核心一句 |
|---|---|---|
| **EP1** | 支持與阻力 S&R | 破局思維：靠 **R:R 3:1** 而非高勝率；**META 多重優勢**；**強勁趨勢六大特徵** |
| **EP2** | 趨勢線 Trend Line | 兩點畫線；四大分類 UTL/TRL/DTL/TSL；越斜越弱；Higher TF 凌駕；Danger Point vs 確認進場 |
| **EP3** | 平衡通道 Channel | 2+1 畫法；通道頂/底 = Short/Long Edge；假突破洗盤；四巫日；多重優勢交易 |
| **EP4** | 移動平均線 × METS | MA 不能單用；**EMA200 牛熊分界**；buy strong / short weak；profit cushion；sell into weakness |

---

## 二、跨集術語對照表（影片概念 → 你 `jlaw_scanner` 的實際代碼）

> 位置：`jlaw_scanner/src/indicators.py`、`scanner.py`、`reporter.py`。✅ = 已實作；🟡 = 部分；⬜ = 未做（未來方向）。

| 影片術語 | 中文 | 定義（JL 原意） | 你的代碼對應 | 狀態 |
|---|---|---|---|---|
| **META / MET Area** | 多重優勢交易區 | 3–5 個不同類型 edge 在同一價位 cluster | `analyze()` 的 `edges` 列表 + `score`；`min_edges` 閘（預設 3） | ✅ |
| **METS** | 多重優勢交易策略 | 九大優勢組成的完整系統 | 整個 `indicators.py` 框架 | ✅ |
| **S&R** | 支持與阻力 | 買/沽方捍衛區（區間非線） | `_recent_support_flip()`（阻力 flip 支持）、`_ma_support_zone()` | ✅ |
| **Flip** | 反轉特性 | 阻力穿後變支持 / 支持穿後變阻力 | `_recent_support_flip()` 的 `level` | ✅ |
| **Trend Line (UTL/TRL/DTL/TSL)** | 趨勢線（四類） | 斜向 S&R；兩點畫；越斜越弱 | `_channel_or_tl()`（UTL 近似）、swing high/low 阻力 | 🟡 |
| **Channel** | 平衡通道 | 平行兩線 = 支持/阻力帶 | —（EP3，未實作） | ⬜ |
| **MA 10/20/50/200** | 移動平均線 | 會移動的趨勢線；支持/阻力區間 | `_add_mas()` + `ema200_filter` + `ma_zone` | ✅ |
| **EMA200 閘** | 牛熊分界 | 大盤/個股在 200MA 下支持不可靠 | `config.ini` `[macd] ema200_filter=true` | ✅ |
| **RS（照妖鏡）** | 相對強度 | 相對大市指數的強弱（跌市找強股） | `relative_strength()` 的 `RS%` | ✅ |
| **強勁趨勢六大特徵** | — | HH/HL 結構、大陽大陰+裂口、量價配合、follow-through、貼快線、高時間效益 | **`strong_trend_features()`（本輪新實作，見第四節）** | ✅ NEW |
| **Base / Cup-with-Handle / VCP** | 底部形態 | 由 S&R 形成的整固（杯柄、波動收縮） | `patterns.detect_base_pattern()` | ✅ |
| **Shakeout / Overshoot / 洗盤** | 假突破震倉 | 短暫穿位後 0–3 支內快速修正 = 多一重優勢 | `_low_volume_pullback()`（低量拉回當確認 edge） | ✅ |
| **R:R 3:1** | 回報風險比 | 每冒 1 元風險計劃賺 3 元 | `analyze()` 的 `stop=support×0.985`、`target=entry+3×risk` | ✅ |
| **Higher TF 凌駕** | 高週期優先 | 月>周>日>小時；Higher TF 的 S&R 更大 | 掃描用日線 + 過濾大盤股（市值門檻） | 🟡 |
| **Danger Point vs 確認進場** | 危險點 vs 順勢 | 逆勢低量觸線即入 vs 確認反彈後入 | —（進場時機未區分） | ⬜ |
| **Buy Strong / Short Weak** | 買強沽弱 | 牛市對沖：強股持有、弱股沽空 | 純多頭，無 SHORT 分支 | ⬜ |
| **Sell into weakness / strength** | 趁弱/趁強離場 | 跌破關鍵位才走 / 到阻力區減持 | `portfolio.json` 追蹤（僅提示，無自動離場邏輯） | 🟡 |
| **Profit Cushion** | 利潤抵消風險 | 用利潤（非本金）去 take risk | — | ⬜ |
| **Second Chance** | 二次機會 | 突破後常拉回給第二次進場 | —（可加「拉回 50MA + 支持區」edge） | ⬜ |

---

## 三、九大優勢實施進度（JL 聲稱 METS 由九大優勢組成）

| # | 優勢 | EP | 你的實作 |
|---|---|---|---|
| 1 | 動能與趨勢（Momentum & Trend） | EP1 | ✅ `trend_structure()` + 強勁趨勢六大特徵 |
| 2 | 支持與阻力（S&R） | EP1/EP4 | ✅ flip / MA zone / `_ma_support_zone` |
| 3 | 趨勢線（Trend Line） | EP2 | 🟡 `_channel_or_tl`（UTL 近似） |
| 4 | 平衡通道（Channel） | EP3 | ⬜ |
| 5 | 移動平均線（MA） | EP4 | ✅ `_add_mas` + `ema200_filter` |
| 6 | 成交量（Volume） | EP1/EP3 | ✅ `_low_volume_pullback` + F3 量價 |
| 7 | 形態（Base / 杯柄 / VCP） | EP1/EP2 | ✅ `patterns.detect_base_pattern` |
| 8 | 相對強度（RS） | EP2/EP4 | ✅ `relative_strength()` |
| 9 | 多週期 / 季節性 | EP3/EP4 | 🟡 日線 + 市值過濾（未做周線/四巫日） |

---

## 四、本輪新增：EP1「強勁趨勢六大特徵」量化規則（已實作）

**函式**：`indicators.strong_trend_features(df, cfg)` → 回傳 6 項子分數 + `count` + `score` + `is_strong`。

**六大特徵與量化定義**（閾值可由 `config.ini [strong_trend]` 覆寫）：

| 特徵 | 量化邏輯 | 預設閾值 |
|---|---|---|
| **F1** 上升結構 HH/HL | 價格在 50/200MA 上 + 近 60 日創高次數 ≥ 創低次數 | 必須為 True |
| **F2** 大陽/大陰燭 + 裂口 | 平均真實實體佔比（0.6 權重）+ 平均裂口幅度（0.4 權重） | body ≥ 2%、gap ≥ 1% |
| **F3** 升日大成交 / 跌日低量 | 升日成交量 ÷ 跌日成交量 | 比 ≥ 1.0（≥2 倍拿滿分） |
| **F4** 燭身少重疊 + follow-through | 連續兩日同向上漲佔比（0.5）+ 實體不重疊佔比（0.5） | — |
| **F5** 貼 10/20MA（多頭排列） | 價格在 20/50MA 上 + 10≥20≥50 排列 + 20MA 近 10 日向上 | 三者皆真 = 1.0 |
| **F6** 短線高時間效益 | 近 20 日回報 | ≥ 20% 拿滿分 |

**判定**：`is_strong = F1 為 True 且 達標特徵數(count) ≥ min_features(預設 5)`。
**效果**：當 `is_strong=True`，`analyze()` 會多輸出一條 edge「強勁趨勢 (X/6 特徵, score=Y.Z): …」，並在 Web 詳情頁 `detail.html` 顯示六大特徵明細表。

> 設計取捨：F5 不要求「貼近」均線——JL 強調強勢股要麼貼快線當支持、要麼強到連 10MA 都不碰；故以「站在多條 MA 之上 + 多頭排列 + 20MA 向上」為判據，避免把強勢拉離均線的股誤判為非強勢。

---

## 五、如何使用 / 後續可擴展

- 跑掃描：`python run.py scan --email`（已自動載入 `[strong_trend]` 設定）。
- 調敏感度：改 `config.ini [strong_trend]` 的 `min_features`（調低→更多股被判強勢）、`body_thresh`/`vol_ratio_thresh`/`time_eff_ret` 等。
- 想看單股六大特徵：啟動 Web（`python run.py schedule` 或 web app）→ 詳情頁。
- 未實作項（⬜）可作下一輪：Channel 通道、SHORT 分支（buy strong/short weak）、Danger Point 進場時機、Second Chance 拉回 edge、周線 Higher-TF 過濾、四巫日成交量忽略。
