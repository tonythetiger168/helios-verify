# Dynamis 交易證據閘門

Basanos 現已接入此 gate：tinyalu 真實 pyuvm/Icarus 500 筆，以獨立 Python ALU 重算結果，再驗等價／錯值／缺筆三種輸出。證據相鄰 basanos/build/ai-eda-integration，契約 basanos/INTEGRATION.md。這不將 Basanos 模擬結果自動變成校準標籤；Minisim 對同 RTL 尚為 unsupported。

使用相鄰 tonythetiger168/dynamis 的 TX comparator，基線 2ba99a980913679a2fdd8b156d564c4693e1da9c。相鄰 Minisim 已完成 288 筆 reference trace 的格式轉接及 9 個正反例閘門；詳見 minisim/docs/DYNAMIS_INTEGRATION.md。

```sh
python scripts/run_dynamis_gate.py golden.jsonl run.jsonl --dynamis ../dynamis --output build/tx-gate
```

返回 0 等價、1 有差異、2 輸入／工具錯誤。要求非空 JSONL、有效 kind／整數時間／key=value payload，拒絕空或重複 field key；明確使用 --with-time。輸出 evidence.json、diff.json、gate.log，包含 input SHA、comparator SHA、Dynamis revision。輸出不可覆寫任一輸入。

已實測 5/5：等價／錯誤值／缺交易／空輸入／防輸入覆寫。結果在 build/dynamis-gate。這是 Dynamis 的 per-kind ordered transaction 比較，不是完整設計正確性證明，也不自動寫入 calibration label。跨 kind 順序、原始 scheduler event 與 time-unit 對齊仍須依 DUT 協定明訂。
