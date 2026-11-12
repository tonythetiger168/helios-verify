# AI EDA 持續改進計劃

新增 FPGA 工具研究與評估：官方 GitHub 工具清單／revision 及「功能、時序約束下最少資源與最小 bitstream」驗收契約見相鄰 minisim/docs/FPGA_TOOLS.md。先以已實測 iCE40 mapping→P&R→pack 基線作為報告格式，再對接 Helios 候選排名；本輪未寫入 calibration 標籤。固定器件的 LUT 減少不保證 bitstream 變小，必須保留兩項獨立目標與 Pareto 候選。

更新：2026-11-01。每日研究見 research/，以來源 URL 與版本日期去重。排序為工程判斷，非外部研究保證；優先確保實測結果可靠。

同日跟進：Windows PATH 沒有工具，但已確認 WSL Ubuntu 有工具鏈，並實際執行本 repo 的 make lint sim synth 成功（Yosys 0.52）。這是 ALU demo 驗收，不是外部協議 corpus 配對驗收，後者仍待執行。Minisim 已加入工作區，詳見 ../minisim/docs/DEVELOPMENT_PLAN.md（相對於 workspace）。C++ lower.hpp 仍未編譯驗收；另一路 Python/pyslang 有限子集前端已完成 counter RTL 執行、來源行映射及 adaptive coverage 驗證，尚不可直接取代 Icarus 或當 Helios 校準 oracle。

14:05 增量里程碑：Minisim 的生成 RTL 模型、golden scoreboard 與 coverage-driven 刺激已接合；291 筆結果在 Icarus／Verilator 同時一致，錯誤設計與 stall 都按指定原因拒絕。這只驗收 counter 子集，尚未把 Minisim 的通過結果餵入本 repo 的 calibration_log。後續跨 repo 接線仍須支援子集、manifest、unsupported 與失敗分類契約。

| 優先 | 項目／狀態 | 預期效益 | 成本／風險 | 驗收指標 |
|---|---|---|---|---|
| P0 | corpus clean 崩潰及 Makefile 契約：本地修復 | 解除校準輸入阻斷 | 低／需真實工具再驗 | 3 個 runner 測試通過；真實 clean/mutant 尚待驗 |
| P0 | 探測可用工具環境並跑真實協議配對 | 分開環境錯誤與設計失敗 | 中／Windows 工具鏈與 Makefile 相依 | 記錄工具版本、指令、退出碼；至少 1 clean PASS、1 已知 mutant FAIL；原始來源無變更 |
| P0 | formal 結果與拒絕條件審查 | 避免錯誤證明污染信心校準 | 中／Yosys 版本及日誌差異 | 針對無 assertion、反例、未證明 equiv、timeout、工具缺失及真實成功各設 fixture；失敗不能成為 PROVED；真實 Yosys 重現 |
| P1 | corpus 配對標籤與可重現 manifest | 比較 rule/Helios router 並避免資料洩漏 | 中／需外部 corpus 與真實模型 | 固定 repo SHA、工具版本、設定、sample id 與家族切分；報告配對數與漏配數；mock 不混入真實 M2 評測 |
| P1 | 波形／交易與 RTL 版本證據索引 | 讓 triage 可定位與重跑 | 中／資料量及 schema | 用固定失敗樣本定位訊號、時間窗及 RTL SHA；評估定位準確率與查詢延遲，再決定 SQLite 導入 |
| P2 | 綜合與 PPA 評測 | 把功能正確與最佳化效果分開 | 高／需 Liberty、約束與後端工具 | 固定製程庫、時脈與 seed；先等價通過，再報面積／時序；無實體資料不聲稱功耗改善 |

每日原則：有依據才改程式；延續進度並執行相關測試。外部依賴不可用時記錄限制，完成可做的研究或評測設計，不把 mock 當真實驗收。不自行 push、merge、發布或開啟付費服務。
