# 参考操作记录与提交顺序

`ReferenceOperationTransactions` 复用调用方已有 SQLite operations 表，不创建数据库、线程、世界或设备。调用方须先验证请求，并持有同一设施及世界锁，覆盖查重、领域准入和执行全过程；参数与执行身份在此期间不得改变。

- `lookup(payload, execution_identity)` 保留原规范序列化检查，匹配参数指纹和执行身份。无记录返回 None；受理后没有终态返回 unknown，不能按新动作重发；已完成/失败/未知的既有回执原样读回。
- `execute_new(..., before, perform)` 在同一事务插入 in_progress、执行 before 账本回调并保存 before checkpoint，提交后才调用 perform。perform 写入领域状态/实际效果和响应，随后 after checkpoint 与终态回执在第二次事务共同提交。
- before 回调不能派发模型效果；perform 负责区分 completed/failed/unknown。SDK 不把失败或未知自动变成可重试。重复 operation_id 在进入回调/效果前由唯一键拒绝。
- 效果阶段异常先回滚 SQL，再调用 restore_committed，还原已提交模型；还原异常向调用方传播。受理阶段异常遵循原边界，由外层宿主恢复此前准入可能改变的内存。不能在无关未提交事务中调用，避免隐式提交其他业务。

授权、样品/几何准入、实际派发及控制器代次由调用方组合。可选 authority_current 在受理提交之后生成回执授权信息，位置与原实现相同。此类不验证 OS 执行身份真实性，也不能自动停止或隔离外部设备。还原失败后的完整设施隔离仍是宿主责任；迁入宿主的这一异常路径尚未全面补齐，不授予生产故障安全资格。

来源为 Testbed 7d4ec1f 的固定板恢复宿主。提取前后 before 与 perform 的领域正文 AST 相同，其他32个宿主方法保持。直接测试使用真实SQLite两连接和明确内存替身，检查提交可见性、参数/身份冲突、结果未知、SQL/序列化中断和还原失败；正式宿主另以真实CPU模型验证。未改旧表、启动JSON、OS或服务。
