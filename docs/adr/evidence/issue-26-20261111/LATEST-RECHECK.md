# 最新 main 复验记录（2026-11-11）

## 已完成

- 远端 main 已更新为 `2602ad7e66f3af28f52f42b6a10a187fbcb8a9da`，包含 #64；此次在第二个隔离工作树核验，原始 B2 分支及上一份证据均保留。
- 相比前次被测版本 d64c83f，`web/js/lineage.js` 没有差异。不能仅据无差异视为重新通过截图验收。
- 当前版本直接运行 `scripts/seed_data.py`，新建独立合成库；真实API返回191/191节点、542边、未截断。
- 当前版本 `tests/test_lineage.py` 聚焦回归：22 passed in 1.71s。未跑全仓或跨平台测试。
- 普通Chrome打开当前版本血缘页，页面可访问性树显示191节点数据，以及测点/数据集/报表/任务/API的类型、业务键、负责人。

## 尚未完成，不冒充通过

- 新一轮全图截图及原生 hover 气泡的人工可见证据未完成。桌面Chrome最小化后恢复失败（foreground window did not report a process id）；浏览器通道随后报 Browser is not available。未注入自制气泡、未伪造截图、未将DOM字段存在改写成气泡目视通过。
- 前次1440×1080截图仍只对应 d64c83f，见同目录 README.md，不将旧图冒称2602ad7新图。
- 上次创建草稿PR及 #26 评论已明确返回GitHub集成403 Resource not accessible by integration。未发现权限恢复证据，本次未盲目重复写操作，也未换身份绕过。
- #26 保持open，0评论。证据分支已存在，但尚未成功挂入issue或创建PR；需要恢复可用发布权限后继续。

## 下一步

1. 普通可见浏览器恢复后，补当前版本全图和五类节点原生 hover 气泡截图。
2. 恢复GitHub集成发布权限后，创建草稿PR并在 #26 挂证据链接，等待评审，不自动合并或关闭。
