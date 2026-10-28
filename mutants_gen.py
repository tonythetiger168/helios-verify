"""
mutants_gen.py — mutants.json 生成器 (helios-verify)

為 corpus_runner / orca_tasks 產生負樣本注入檔。兩種模式：

A. --from-git（推薦）：掃 git history 的修 bug commit，
   用 parent commit 的檔案版本當 buggy_content。
   每個 mutant = 「修復前的版本」，revert 後驗證應 fail。

B. --from-json：手工清單（bug id、影響檔案、buggy 內容），
   適合 bug 修復歷史不在 git 裡的情況。

輸出格式（兩種 corpus runner 共用）：
{
  "mutants": [
    {"id": "M001", "affected": ["UART"],          # corpus_runner 用
     "affected_files": ["rtl/uart/uart_top.sv"],  # orca_tasks 用
     "buggy_content": "<revert 後的完整檔案內容>",
     "source_commit": "abc123", "note": "..."}
  ]
}

用法：
  # ORCA：從 git history 自動產生（需 git log 裡的 fix commit）
  python mutants_gen.py --repo ../orca-v63 --from-git --out orca_mutants.json

  # open-ip-portfolio：同理
  python mutants_gen.py --repo ../open-ip-portfolio --from-git --out mutants.json

  # 只取最近的 N 個 fix commit（試點用）
  python mutants_gen.py --repo ../orca-v63 --from-git --max 8

  # 手工模式
  python mutants_gen.py --from-json my_bugs.json --out mutants.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def git(*args: str, cwd: Path) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


# ---------- A. 從 git history ----------

FIX_PAT = re.compile(r"\b(fix|bug|bugfix|patch|repair)\b", re.IGNORECASE)


def find_fix_commits(repo: Path, max_n: int, since: str = "") -> list[str]:
    """git log 找標題含 fix/bug 的 commit（新到舊）。"""
    args = ["log", "--format=%H %s", "--no-merges"]
    if since:
        args += ["--since", since]
    log = git(*args, cwd=repo)
    hits = [ln.split()[0] for ln in log.splitlines()
            if FIX_PAT.search(ln.split(" ", 1)[1] if " " in ln else "")]
    return hits[:max_n] if max_n else hits


def gen_from_git(repo: Path, max_n: int, rtl_only: bool = True) -> list[dict]:
    mutants = []
    for sha in find_fix_commits(repo, max_n):
        subject = git("log", "-1", "--format=%s", sha, cwd=repo).strip()
        diff_files = git("diff-tree", "--no-commit-id", "-r", sha,
                         cwd=repo).splitlines()
        # 只取程式碼檔；rtl_only 時限定 rtl/ 目錄
        files = [ln.split("\t")[-1] for ln in diff_files if ln]
        if rtl_only:
            files = [f for f in files if f.startswith(("rtl/", "tb/"))]
        if not files:
            continue
        parent = git("rev-parse", f"{sha}^", cwd=repo).strip()
        entry = {"id": f"M{len(mutants)+1:03d}",
                 "affected": [],               # corpus_runner 欄位（下層填）
                 "affected_files": files,
                 "buggy_content": {},
                 "source_commit": sha,
                 "note": subject}
        for f in files:
            blob = git("show", f"{parent}:{f}", cwd=repo)
            entry["buggy_content"][f] = blob
        mutants.append(entry)
    return mutants


# ---------- B. 手工模式 ----------

def gen_from_json(src: Path) -> list[dict]:
    data = json.loads(src.read_text())
    out = []
    for i, b in enumerate(data.get("bugs", [])):
        files = b["files"] if isinstance(b["files"], list) else [b["file"]]
        content = {}
        for f, c in (b.get("buggy_content") or {}).items():
            content[f] = c
        out.append({"id": b.get("id", f"M{i+1:03d}"),
                    "affected": b.get("affected", []),
                    "affected_files": files,
                    "buggy_content": content,
                    "source_commit": b.get("source_commit", "manual"),
                    "note": b.get("note", "")})
    return out


# ---------- 寫檔 ----------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, help="git repo 路徑（--from-git 必填）")
    ap.add_argument("--from-git", action="store_true")
    ap.add_argument("--from-json", type=Path, help="手工 bug 清單")
    ap.add_argument("--max", type=int, default=0, help="最多取 N 個 fix commit")
    ap.add_argument("--all-files", action="store_true",
                    help="不限定 rtl/（預設只挑 rtl/ 與 tb/ 的變動）")
    ap.add_argument("--out", default="mutants.json")
    args = ap.parse_args()

    if args.from_git:
        if not args.repo:
            sys.exit("--from-git 需要 --repo")
        mutants = gen_from_git(args.repo, args.max, rtl_only=not args.all_files)
    elif args.from_json:
        mutants = gen_from_json(args.from_json)
    else:
        sys.exit("請指定 --from-git 或 --from-json")

    if not mutants:
        sys.exit("找不到符合條件的 fix commit —— 檢查 git log 標題是否含 fix/bug，"
                 "或改用 --from-json 手工指定")

    Path(args.out).write_text(json.dumps({"mutants": mutants}, indent=2))
    print(f"已產生 {len(mutants)} 個 mutant -> {args.out}")
    for m in mutants:
        print(f"  {m['id']} [{m['source_commit'][:7]}] "
              f"{len(m['affected_files'])} file(s): {m['note'][:60]}")


if __name__ == "__main__":
    main()
