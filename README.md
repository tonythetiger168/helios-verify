# helios-verify

自研驗證 Agent 平台（Verifier 層 + τ 校準閉環 + Phase 1 Triage Agent）。
Helios 家族核心 repo：大腦（helioslm）＋身體（本 repo）。

## Repo 家族

| Repo | 角色 |
|---|---|
| [helioslm](https://github.com/tonythetiger168/helioslm) | Confidence Router 大腦（certified-confidence routing） |
| **helios-verify**（本 repo） | 驗證平台主體（Verifier Gate / corpus / triage / τ 校準） |
| [dynamis](https://github.com/tonythetiger168/dynamis) | 語意級 oracle（txdiff）＋ transaction 觀測空間 |
| [open-ip-portfolio](https://github.com/tonythetiger168/open-ip-portfolio) | 練兵場：117 個協議切片（含 16 個 mutant-proven bug） |
| [open-vip-portfolio](https://github.com/tonythetiger168/open-vip-portfolio) | 知識庫：109 UVM VIP（protocol KG＋語料） |
| [orca-v63](https://github.com/tonythetiger168/orca-v63) | 實戰目標：49 模組 RISC-V SoC（67% coverage hole） |

## 架構

```
Patch (LLM Agent)
   │
   ▼
VerifierGate ── 1. 靜態檢查 ──▶ verilator --lint-only + yosys synth
   │              2. Router ──▶ RuleRouter（M2 換 HeliosLM scorer）
   │              3. 模擬 ────▶ iverilog + vvp（make sim）
   │              4. 形式驗證 ─▶ yosys sat（可選 equiv）
   ▼
FeedbackRecord ──▶ calibration_log/ ──▶ tau_calibrate.py（累積 ≥100 筆後校準 τ）
```

## 快速開始

相依：python3.10+、verilator、iverilog、yosys

```bash
# 手動驗證工具鏈
make lint sim synth formal

# 跑 VerifierGate demo（fast/deep route 實例）
python verifier_gate.py

# 看產生的 FeedbackRecord
ls calibration_log/

# 校準（<100 筆時只出報告、不動 τ）
python tau_calibrate.py
cat tau_report.json

# --- corpus 模式（open-ip-portfolio / orca-v63）---
git clone https://github.com/tonythetiger168/open-ip-portfolio.git ../open-ip-portfolio
git clone https://github.com/tonythetiger168/orca-v63.git ../orca-v63

# 產生負樣本（從 git history 的 fix commit）
python mutants_gen.py --repo ../open-ip-portfolio --from-git --out mutants.json

# 協議切片 corpus
python corpus_runner.py --ip-root ../open-ip-portfolio --proto UART --oracle ../dynamis

# ORCA macro-task（先看 subset 對應）
python orca_tasks.py --orca-root ../orca-v63 --list
python orca_tasks.py --orca-root ../orca-v63 --domain cpu --limit 5
```

## 檔案說明

| 檔案 | 作用 |
|---|---|
| `rtl/alu.v` | 示範 DUT |
| `rtl/tb_alu.v` | 自我檢查 testbench（印 TEST PASS / FAIL） |
| `files.f` | Verilator / Yosys 共用的檔案清單 |
| `verifier_gate.py` | Verifier 層主體；`RuleRouter.route()` 是 M2 換 HeliosLM 的唯一介面 |
| `tau_calibrate.py` | τ 校準 job：ECE / fail rate / 單調更新 / 審計軌跡 |
| `tau_config.json` | 初始 τ 閾值 |
| `calibration_log/` | 每次驗證的 FeedbackRecord（校準資料集） |
| `corpus_runner.py` | open-ip-portfolio batch runner（`--oracle` 啟動 dynamis 語意判定） |
| `orca_tasks.py` | ORCA v6.3 macro-task（模組→TB subset 對應、per-module mutant） |
| `mutants_gen.py` | mutants.json 生成（`--from-git` 掃 fix commit / `--from-json` 手工） |
| `tx_adapter.py` | dynamis txdiff 語意 oracle（自動 fallback 字串比對） |
| `tx_inject.py` | testbench 的 tx_record 注入（recorder 產生 + diff 預覽） |
| `helios_router.py` | M2 HeliosLM Confidence Router（mock/helioslm 雙 scorer，降級安全） |
| `triage_agent.py` | Phase 1 Regression Triage Agent（分類+最小重跑自我驗證） |

## M2 接 HeliosLM

```python
class HeliosRouter(RuleRouter):
    def route(self, patch, sig):
        conf = helios.score(features_from(sig, patch.diff))  # logit threshold，勿用 JSON 輸出
        # τ 邏輯其餘不變
```

校準標籤： `tau_report.json` 的 `calibration_curve`（avg_conf ↔ 實測 acc）
即 HeliosLM domain fine-tune 的訓練資料來源。

## 已知限制（M1 刻意為之）

- `yosys_formal` 無 golden 時跑 SAT assertion check，目前 DUT 無 assertion → 預期 UNKNOWN
- `regression_subset` 目前是 placeholder，之後換成 module DAG 影響分析
- 模擬走 iverilog（完整 SV 時序支援）；Verilator 留給 lint/高速 regression
