# AI-EDA Handover — helios-verify 專案交接總覽

**版本：v2.0 ｜ 日期：2026-10-28 ｜ 整理人：Tony（GitHub: tonythetiger168）**
**聯絡窗口：接手問題請透過 GitHub issue 或站内訊息聯繫**

---

## 0. 現況總結（2026-10-28）

| 項目 | 狀態 |
|---|---|
| 程式碼 | v1.0 完整快照存在本備份包（9 支工具齊全） |
| GitHub repo | **仍是空殼**——只有 LICENSE，code 從未 push |
| M1 RuleRouter | 程式完成，calibration 資料待累積 |
| M2 HeliosLM | 介面就緒，差 `HELIOSLM_MODEL_CLASS` 設定值 |
| τ 校準 | 等待 ≥100 筆（建議 300 筆）真實資料 |
| 校準相容性驗證 | 方案已定（Phase 0–4），分析腳本 `compat_check.py` 已完成 |
| Phase 1 Triage | 程式完成，待真實失敗 log 驗證準確率 |

**最優先事項：把本備份包的 code push 上 GitHub。** 過去一個月所有成果只有本機副本，無異地備份。

---

## 1. 一句話定位

自我驗證的 Agentic EDA 驗證平台：LLM Agent 的每個 patch 都經確定性引擎驗證，
信心分數由可校準的 HeliosLM Router 路由，全部決策留有審計軌跡。
對標 Synopsys / Cadence / Siemens / 合見 / 華大九天 / 芯華章 / 芯和 七家 Agentic EDA，
差異：全開源、無 vendor lock-in、confidence routing 第三方可驗證。

## 2. Repo 家族（六個，皆 Apache-2.0）

| Repo | 角色 | 狀態 |
|---|---|---|
| **helios-verify** | 平台主體 | v1.0 code 完成，**待 push** |
| helioslm | Confidence Router 大腦（char-level LM，8.5M，v5.31） | 自研，BPE 版待訓 |
| dynamis | 語意 oracle（txdiff）＋ DPI-C transaction recorder | 自研 |
| open-ip-portfolio | 練兵場：117 協議配置 / 81 cores，16 個 mutant-proven bug | v2.5.1 |
| open-vip-portfolio | 知識庫：109 UVM VIP＋22 RTL IP | v1.7.5 |
| orca-v63 | 實戰目標：49 模組 RISC-V SoC，67% coverage hole | v6.3 |

## 3. 架構

```
LLM Agent（任務產生 patch）
   │
   ▼
VerifierGate（verifier_gate.py）
   ├─ 1. 靜態檢查（永遠跑）：Verilator lint + Yosys synth
   ├─ 2. Router 路由（helios_router.py）
   │     conf ≥ τ_fast → fast_path（不跑 formal）
   │     conf < τ_reject → reject（連模擬都不跑）
   │     其餘 → deep_verify（模擬＋formal）
   │     conf 來源：M1 RuleRouter（啟發式）/ M2 HeliosLMScorer（logit 二分類）
   ├─ 3. 模擬：make sim（iverilog）／dynamis txdiff 語意判定（--oracle 啟用）
   └─ 4. 形式驗證：Yosys SAT / equiv（deep_verify 或 conf < 0.3）
   ▼
FeedbackRecord → calibration_log/ → tau_calibrate.py
                 （≥100 筆後校準 τ：ECE、fail rate、嚴格單調更新、tau_history 審計）
```

**核心原則**（與七家商用平台對應）：
- Siemens 自我驗證 → Verifier Gate 全確定性引擎，LLM 不參與正確性判定
- Cadence Mental Model → open-vip-portfolio 的 protocol 知識（KG 待建）
- Synopsys 廣度 → 協議切片＋真實 SoC 雙 corpus
- 合見「證據鏈」→ FeedbackRecord＋tau_history 全程可審計，且第三方可驗證

## 4. 工具清單（9 支程式）

| 程式 | 功能 | 狀態 |
|---|---|---|
| verifier_gate.py | Verifier 層主體；`RuleRouter.route()` 是換 HeliosLM 的唯一介面 | ✅ M1 |
| tau_calibrate.py | τ 校準：ECE、bucket fail rate、嚴格單調更新、tau_history 審計 | ✅ 待命 |
| corpus_runner.py | open-ip-portfolio batch | ✅ |
| orca_tasks.py | ORCA macro-task | ✅ |
| mutants_gen.py | mutants.json 產生 | ✅ |
| helios_router.py | M2 Router：HeliosLMScorer／MockScorer／ConstantScorer | ✅ 介面就緒 |
| triage_agent.py | Phase 1 Regression Triage | ✅ mock LLM 可用 |
| tx_adapter.py | dynamis txdiff 語意 oracle | ✅ |
| tx_inject.py | testbench tx_record 注入 | ✅ 待試點 |

## 5. 校準相容性驗證方案（M1→M2，本次新增）

**問題**：verdict 標籤可重用（來自確定性引擎），但 conf 值不可——M1 啟發式分數與
M2 logit 分數的分布、校準性質不同，τ=0.7/0.15 不能硬轉。

**配套工具：`compat_check.py`**（在本備份包根目錄）
讀入 `--router rule` 與 `--router helios` 兩份 calibration_log，輸出：
Spearman ρ + Bootstrap 95% CI、分區間 ρ、ECE 比較、兩種 τ 映射
（分位數映射 + isotonic regression）、decision agreement + McNemar test、
五項 go/no-go 閘門。exit 0 = 相容；exit 1 = 走 shadow mode。

**執行步驟**：

```bash
# Phase 0：產生配對資料（同一批 mutants 跑兩輪）
python corpus_runner.py --ip-root ../open-ip-portfolio \
  --proto UART --mutants mutants.json --router rule  --out calibration_log/cal_rule.jsonl
python corpus_runner.py --ip-root ../open-ip-portfolio \
  --proto UART --mutants mutants.json --router helios --out calibration_log/cal_helios.jsonl

# Phase 1+2：檢定
python compat_check.py --rule-log calibration_log/cal_rule.jsonl \
  --helios-log calibration_log/cal_helios.jsonl --tau-fast 0.7 --tau-reject 0.15
```

**判定與路線**：
- ρ ≥ 0.8 且閘門全過 → τ 映射可用，但仍建議 shadow 300 筆再正式切換
- ρ < 0.5 → M1 資料對 M2 τ 校準作廢，M2 shadow mode 累積 ≥300 筆重校

**Shadow mode 資料格式約定**（calibration_log 每筆）：
```json
{"patch": "uart_rx_mutant3", "scorer_version": "helioslm_v5.31",
 "conf_rule": 0.62, "conf_helios": 1.83, "verdict": "pass", "routed_by": "rule"}
```
`verdict` 永遠來自 VerifierGate 完整管線；`conf_helios` 只記錄不影響決策。

## 6. M2 啟用方法

```bash
export HELIOSLM_ROOT=../helioslm
export HELIOSLM_CKPT=checkpoints/chat_tuned_v5.30.2.pt
export HELIOSLM_MODEL_CLASS=<模組路徑>:<模型類別名>   # 依 helioslm repo 實際結構填
export HELIOSLM_PASS_CLASS=1
python corpus_runner.py --ip-root ../open-ip-portfolio --router helios
```

規格約定：logit threshold 二分類、不輸出 JSON、載入失敗自動降級 MockScorer 並警告。
注意：若 `HELIOSLM_MODEL_CLASS` 未設，跑出的 conf 是 MockScorer 而非 M2 分數，
compat_check 結果無效。

## 7. 已知限制與風險

| 項目 | 說明 | 對策 |
|---|---|---|
| **GitHub 仍是空 repo** | code 只有本機/本備份包一份 | **第一優先：push 上 GitHub** |
| iverilog 無 DPI | dynamis tx_record 需 Verilator/VCS/Xcelium | 錄製版走 verilator --binary --timing |
| UVM VIP 非完整模擬 | open-vip-portfolio 開源工具僅 elaboration/smoke | 先當 KG 知識源；cocotb 移植備案 |
| tx_inject 寬度啟發式 | data/addr 給 32bit，其餘 1bit | 套用前必看 diff；AXI 人工確認 |
| ORCA 相依複雜 | orca_pkg / flit_t / MESI-F 跨模組 | mutant 按模組邊界設計 |
| 前 100 筆校準資料 | τ 初始值 0.7/0.15 是估的 | 真實 regression 場景累積，別只餵 demo |
| HeliosLM checkpoint | 模型類別以環境變數指定 | 對照 helioslm repo 實際結構確認 |

## 8. Roadmap

- **Phase 1（0–3 月）Regression Triage Agent**：程式完成，待真實失敗 log 驗證
- **Phase 2（3–6 月）Coverage Closure Agent**：未開始（open-ip-portfolio 已閉合 coverage ＋ ORCA 67% hole）
- **Phase 3（6–12 月）Long-Horizon Agent**：未開始
- **M2 HeliosLM**：差 `HELIOSLM_MODEL_CLASS` 設定值＋相容性驗證

## 9. 安全政策

- 認證一律走本機 SSH key，任何 token 不得出現在對話或文件中
- push 前確認 `.gitignore` 排除：`calibration_log/`、`mutants.json`、`*.pt`、`__pycache__/`

## 10. 接手人第一件事（依序）

1. `git status` 確認本機 code 完整，建立 `.gitignore`
2. **push v1.0 上 GitHub**（見第 7 節風險表第一列）
3. `make lint sim` 確認工具鏈
4. Phase 0 跑兩輪 corpus_runner，產生配對資料
5. `python compat_check.py` 做相容性檢定，貼結果給原團隊判讀
6. 依檢定結果走 τ 映射或 shadow mode，累積 300 筆後 `tau_calibrate.py`
7. 確認 `HELIOSLM_MODEL_CLASS` 後啟動 M2
