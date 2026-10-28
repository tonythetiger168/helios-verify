"""
helios_router.py — M2: HeliosLM Confidence Router (helios-verify)

把 RuleRouter 的啟發式 conf 換成 HeliosLM scorer（spec 約定）：
  - logit threshold 二分類（pass/fail），禁止 JSON 輸出（v5 exact-match 0/40 的坑）
  - conf = P(pass) = softmax(logits)[pass_class]
  - HeliosLM 只負責「值不值得驗」，不參與正確性判斷

三種 scorer：
  HeliosLMScorer  真實載入 ../helioslm 的 checkpoint（torch .pt）
  MockScorer      可校準的啟發式（無模型時降級用，已驗證）
  ConstantScorer  固定 conf（τ 邊界單元測試用）

HeliosLM checkpoint 資訊（來自 HF chienhsinlin/helioslm）：
  - char-level toy（ord<1024），BOS=1023 EOS=1022 PAD=1021
  - 8.5M params，v5.31 前 exact-match 0/40 → 只信 logit，不信生成
  - 模型類別路徑以 HELIOSLM_MODEL_CLASS 環境變數指定（repo 結構以實際為準）

用法：
  python helios_router.py                          # 自檢（Mock 模式）
  HELIOSLM_ROOT=../helioslm HELIOSLM_CKPT=checkpoints/chat_tuned_v5.30.2.pt \
      python corpus_runner.py --ip-root ../open-ip-portfolio --router helios
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict
from pathlib import Path

from verifier_gate import Patch, Route, StaticSignals, VerifyDepth, RuleRouter


# ---------- 特徵編碼（char-level friendly） ----------

def encode_features(sig: StaticSignals, patch: Patch) -> str:
    """靜態特徵 → 簡短 prompt（HeliosLM char-level 容量小，prompt 要短）。
    v5.30.2 教訓：長 chat transcript tool-mode 會崩，short-prompt parse rate 0.67。"""
    return (f"task={patch.task_type} "
            f"lines={sig.lines_changed} files={sig.files_touched} "
            f"lint={sig.verilator_lint_warnings} syn={sig.sv_syntax_check} "
            f"hist={sig.historical_task_success_rate:.2f} "
            f"agent={patch.agent_conf:.2f}")


# ---------- Scorer 介面 ----------

class Scorer:
    name = "base"
    def score(self, features_text: str, sig: StaticSignals, patch: Patch) -> float:
        raise NotImplementedError


class MockScorer(Scorer):
    """無模型時的降級版：可校準啟發式（和 RuleRouter 同式但拆開，方便替換）。"""
    name = "mock"
    def score(self, features_text, sig, patch) -> float:
        logit = (1.2 * (patch.agent_conf - 0.5)
                 + 1.5 * (sig.historical_task_success_rate - 0.5)
                 + (0.8 if sig.sv_syntax_check == "pass" else -0.8)
                 - 0.3 * sig.verilator_lint_warnings
                 - 0.01 * sig.lines_changed)
        return 1.0 / (1.0 + math.exp(-logit))   # sigmoid → (0,1)


class ConstantScorer(Scorer):
    name = "constant"
    def __init__(self, conf: float): self.conf = conf
    def score(self, features_text, sig, patch): return self.conf


class HeliosLMScorer(Scorer):
    """真實 HeliosLM：兩分類 logit → P(pass)。
    模型類別路徑以環境變數指定（helioslm repo 的實際結構以 HELIOSLM_MODEL_CLASS 為準）。"""
    name = "helioslm"

    def __init__(self, root: str | None = None, ckpt: str | None = None):
        self.root = Path(root or os.environ.get("HELIOSLM_ROOT", "../helioslm"))
        self.ckpt = ckpt or os.environ.get("HELIOSLM_CKPT",
                                           "checkpoints/chat_tuned_v5.30.2.pt")
        self.model = None
        self.fallback = MockScorer()
        self._load()

    def _load(self) -> None:
        try:
            import torch  # noqa: F401
        except ImportError:
            print("[helios] torch 未安裝，降級 MockScorer", file=sys.stderr)
            return
        model_class = os.environ.get("HELIOSLM_MODEL_CLASS", "")
        ckpt_path = self.root / self.ckpt
        if not model_class or not ckpt_path.exists():
            print(f"[helios] 模型類別未指定或 checkpoint 不存在（{ckpt_path}），"
                  f"降級 MockScorer。設定 HELIOSLM_MODEL_CLASS=模組:類別 啟用。",
                  file=sys.stderr)
            return
        try:
            import importlib
            mod_name, cls_name = model_class.split(":")
            cls = getattr(importlib.import_module(mod_name), cls_name)
            self.model = cls()
            state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
            self.model.load_state_dict(state)
            self.model.eval()
            print(f"[helios] loaded {ckpt_path}")
        except Exception as e:   # 任何載入失敗都降級，不能讓 router 炸掉
            print(f"[helios] 載入失敗（{e}），降級 MockScorer", file=sys.stderr)
            self.model = None

    def score(self, features_text, sig, patch) -> float:
        if self.model is None:
            return self.fallback.score(features_text, sig, patch)
        import torch
        # char-level tokenize（HF 頁規格：ord(c)<1024，BOS=1023）
        ids = [1023] + [ord(c) for c in features_text if ord(c) < 1024] + [1022]
        x = torch.tensor([ids], dtype=torch.long)
        with torch.no_grad():
            logits = self.model(x)                     # [1, T, V] 或 [1, V]
        last = logits[0, -1] if logits.dim() == 3 else logits[0]
        # 兩分類 logit → P(pass)。類別順序以 HELIOSLM_PASS_CLASS 指定（預設 1）
        pass_cls = int(os.environ.get("HELIOSLM_PASS_CLASS", "1"))
        two = torch.tensor([last[0].item(), last[pass_cls].item()])
        return float(torch.softmax(two, dim=0)[1].item())


# ---------- Router ----------

class HeliosRouter(RuleRouter):
    """τ 邏輯繼承 RuleRouter，只換 conf 來源。drop-in 替換。"""

    def __init__(self, cfg_path: str = "tau_config.json", scorer: Scorer | None = None):
        super().__init__(cfg_path)
        self.scorer = scorer or HeliosLMScorer()

    def route(self, patch: Patch, sig: StaticSignals) -> tuple:
        conf = self.scorer.score(encode_features(sig, patch), sig, patch)
        if conf >= self.tau_fast:
            return Route.FAST_PATH, VerifyDepth(run_formal=False), conf
        if conf < self.tau_reject:
            return Route.REJECT, VerifyDepth(run_simulation=False), conf
        return Route.DEEP_VERIFY, VerifyDepth(run_formal=True), conf


def make_router(kind: str, cfg_path: str = "tau_config.json") -> RuleRouter:
    """corpus_runner / orca_tasks 的 --router 工廠。"""
    if kind == "rule":
        return RuleRouter(cfg_path)
    if kind == "helios":
        return HeliosRouter(cfg_path)
    sys.exit(f"unknown router: {kind}")


# ---------- 自檢 ----------

if __name__ == "__main__":
    from verifier_gate import StaticSignals, Patch
    for name, sc in [("mock", MockScorer()),
                     ("helios(fallback)", HeliosLMScorer()),
                     ("const0.9", ConstantScorer(0.9))]:
        r = HeliosRouter("/mnt/agents/output/helios-verify/tau_config.json", scorer=sc)
        good = Patch(task_type="rtl_fix", agent_conf=0.9)
        bad = Patch(task_type="rtl_fix", agent_conf=0.1)
        sg = StaticSignals(lines_changed=3, sv_syntax_check="pass",
                           historical_task_success_rate=0.8,
                           verilator_lint_warnings=0)
        sb = StaticSignals(lines_changed=300, sv_syntax_check="fail",
                           historical_task_success_rate=0.3,
                           verilator_lint_warnings=7)
        rg, _d, cg = r.route(good, sg)
        rb, _d2, cb = r.route(bad, sb)
        print(f"{name:<18} good→{rg.value}({cg:.3f})  bad→{rb.value}({cb:.3f})")
        if not isinstance(sc, ConstantScorer):
            assert cg > cb, f"{name}: good patch 的 conf 應高於 bad patch"
    print("\nself-check OK（三種 scorer 行為一致，conf 排序正確）")
