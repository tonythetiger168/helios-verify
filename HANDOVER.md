# helios-verify 交接文檔

**版本：v1.0 ｜ 日期：2026-09-30 ｜ 狀態：M1 完成、M2 介面就緒、已上架 GitHub**

---

## 1. 一句話定位

自我驗證的 Agentic EDA 驗證平台：LLM Agent 的每個 patch 都經確定性引擎驗證，
信心分數由可校準的 HeliosLM Router 路由，全部決策留有審計軌跡。
對標 Synopsys / Cadence / Siemens / 合見 / 華大九天 / 芯華章 / 芯和 七家 Agentic EDA，
差異：全開源、無 vendor lock-in、confidence routing 第三方可驗證。

## 2. Repo 家族（六個，皆 Apache-2.0）

| Repo | 角色 | 狀態 |
|---|---|---|
| **helios-verify** | 平台主體（本 repo） | v1.0 已上線 |
| helioslm | Confidence Router 大腦（char-level LM，8.5M，v5.31） | 自研，BPE 版待訓 |
| dynamis | 語意 oracle（txdiff）＋ DPI-C transaction recorder＋ Python SV 模擬器 | 自研，持續更新 |
| open-ip-portfolio | 練兵場：117 協議配置 / 81 cores，16 個 mutant-proven bug | v2.5.1 |
| open-vip-portfolio | 知識庫：109 UVM VIP＋22 RTL IP，L1a pyslang gate | v1.7.5 |
| orca-v63 | 實戰目標：49 模組 RISC-V SoC＋AI accelerator，67% coverage hole | v6.3 |

GitHub: https://github.com/tonythetiger168/helios-verify

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

## 4. 工具清單（9 支程式，全部通過編譯與單元測試）

| 程式 | 功能 | 狀態 |
|---|---|---|
| verifier_gate.py | Verifier 層主體；`RuleRouter.route()` 是換 HeliosLM 的唯一介面 | ✅ M1 |
| tau_calibrate.py | τ 校準：ECE、bucket fail rate、嚴格單調更新（τ_fast 只升、τ_reject 只降）、tau_history 審計 | ✅ 待命（等 100 筆資料） |
| corpus_runner.py | open-ip-portfolio batch（`--proto`/`--family`/`--mutants`/`--oracle`/`--router`） | ✅ |
| orca_tasks.py | ORCA macro-task：模組→TB subset 對應、per-module mutant、`--list` 預覽 | ✅ |
| mutants_gen.py | mutants.json 產生：`--from-git` 掃 fix commit / `--from-json` 手工 | ✅ |
| helios_router.py | M2 Router：HeliosLMScorer（環境變數載入）／MockScorer／ConstantScorer | ✅ 介面就緒 |
| triage_agent.py | Phase 1 Regression Triage：分類＋最小重跑自我驗證，FeedbackRecord 回饋 | ✅ mock LLM 可用 |
| tx_adapter.py | dynamis txdiff 語意 oracle，自動 fallback 字串比對 | ✅ |
| tx_inject.py | testbench tx_record 注入（recorder 產生＋diff 預覽，UART/I2C 試點） | ✅ 待試點 |

## 5. 本地快速開始

```bash
git clone git@github.com:tonythetiger168/helios-verify.git
cd helios-verify

# 環境：python3.10+、verilator、iverilog、yosys、make
make lint sim          # 工具鏈自檢（demo alu）

# Phase 1 試點
python triage_agent.py --log <失敗的sim.log> --workdir <repo> --llm mock

# Corpus（需先 clone 家族 repo 到上層目錄）
python mutants_gen.py --repo ../open-ip-portfolio --from-git --out mutants.json
python corpus_runner.py --ip-root ../open-ip-portfolio --proto UART --mutants mutants.json
python tau_calibrate.py                  # ≥100 筆後開始校準

# ORCA macro-task
python orca_tasks.py --orca-root ../orca-v63 --list
python orca_tasks.py --orca-root ../orca-v63 --domain cpu --limit 5
```

## 6. M2 啟用 HeliosLM 的方法

```bash
HELIOSLM_ROOT=../helioslm \
HELIOSLM_CKPT=checkpoints/chat_tuned_v5.30.2.pt \
HELIOSLM_MODEL_CLASS=<模組路徑>:<模型類別名> \   # ← 依 helioslm repo 實際結構填
HELIOSLM_PASS_CLASS=1 \
python corpus_runner.py --ip-root ../open-ip-portfolio --router helios
```

規格約定：logit threshold 二分類、不輸出 JSON（v5 exact-match 0/40）、
載入失敗自動降級 MockScorer 並警告，流程永不因模型問題中斷。
校準標籤來源：`tau_report.json` 的 calibration_curve（avg_conf ↔ 實測 acc）。

## 7. 已知限制與風險

| 項目 | 說明 | 對策 |
|---|---|---|
| iverilog 無 DPI | dynamis tx_record 需 Verilator/VCS/Xcelium | 錄製版走 verilator --binary --timing；iverilog 留給非錄製 regression |
| UVM VIP 非完整模擬 | open-vip-portfolio 在開源工具上僅 elaboration/smoke | 先當 KG 知識源與語料；cocotb 移植路線備案 |
| tx_inject 寬度啟發式 | data/addr 給 32bit，其餘 1bit，best-effort | 套用前必看 diff；AXI 類寬匯流排人工確認 |
| ORCA 相依複雜 | orca_pkg / flit_t / MESI-F 跨模組 | mutant 按模組邊界設計，勿用協議切片隔離法 |
| 前 100 筆校準資料 | τ 初始值 0.7/0.15 是估的 | 盡量在真實 regression 場景累積，別只餵 demo |
| HeliosLM checkpoint 路徑 | 模型類別以環境變數指定 | 需對照 helioslm repo 實際結構確認 |

## 8. Roadmap 狀態

- **Phase 1（0–3 月）Regression Triage Agent**：✅ 程式完成（triage_agent.py），
  待本機用 open-ip-portfolio 製造失敗跑一次驗證分類準確率
- **Phase 2（3–6 月）Coverage Closure Agent**：⬜ 未開始。
  目標素材：open-ip-portfolio 117 協議（含已閉合 coverage）＋ORCA 67% hole
  （freelist 87% / prf 92% / cmt_rob 78%）
- **Phase 3（6–12 月）Long-Horizon Agent**：⬜ 未開始。
  Verification Engineer Agent（testplan→執行→debug→收斂）＋Design Engineer Agent
- **M2 HeliosLM 上場**：⬜ 只差 HELIOSLM_MODEL_CLASS 設定值

## 9. 重要事件紀錄

- 2026-09-30：GitHub PAT 曾在對話中明文貼出 → **該 token 已要求撤銷**，
  後續認證一律走本機 SSH key，token 不得出現在對話或文件中

## 10. 接手人第一件事

1. `make lint sim` 確認工具鏈
2. `python triage_agent.py --llm mock` 跑 demo log（open-ip-portfolio 製造一個失敗）
3. `python corpus_runner.py --ip-root ../open-ip-portfolio --proto UART` 累積前 100 筆
4. `python tau_calibrate.py` 看第一份 tau_report.json
5. 確認 HeliosLM 模型類別路徑後啟動 M2
