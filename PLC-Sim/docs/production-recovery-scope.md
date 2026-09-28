# 生产执行身份的恢复与撤销范围

0.2.9 的 `reference_execution_scope` 统一普通操作门与恢复设施的声明校验。旧五字段身份仍按 `origin_instance_id` 撤销；生产 binding v1/v2 则按 `(local_device_id, job_uuid)` 撤销同一作业的全部 claim/attempt。claim、fence、设备执行者和本地日志 writer 都不冒充旧进程身份。新作业及其他设备节点不因此被视为同一撤销对象。

`validate_execution_identity` 核对生产操作摘要与实际设备映射；对账还核对请求的 fingerprint。它不验证当前 OS 的 claim 权威。设施仍先验证自身凭证、租约 epoch、world 和过期时间，再核对原操作完整身份。停止、减速和模型时钟仍由设备包及宿主提供。

恢复宿主创建 `revoked_dispatch_jobs(local_device_id, job_uuid)`，保留原 `revoked_origins`。`revoke_execution_scope` 与 `execution_revoked` 使用对应表；`execution_scope_checkpoint` 在同一世界提交中记录两类撤销库存，恢复时必须相等。公共函数不创建连接、不提交事务，也不宣布物理停止。ReferenceRecoveryCoordinator 仍需调用方提供原表、锁、真实控制器读回及 save_durable；生产身份路径额外需要新表。

`Authority(..., dispatch_identities=True)` 显式使用 `labos.plate-authority/v2`。v2 的 operation_execution_scopes 保存完整身份和有类型的撤销范围，随授权 checkpoint 校验。生产行的旧 origin_instance_id 为 NULL；旧身份行保留真实原值。默认 v1 接口/表及其旧调用行为保持，两种 schema 相互拒绝，不原地升级旧库。`revocation_scopes(grant_id)` 供宿主在授权切换的原事务中撤销各范围；控制器停止、样品保留和世界保存仍由原宿主完成。

Testbed 的当前 CPU 恢复宿主已接入以上机制。新的源码身份拒绝旧世界目录，不改写历史源摘要以强行恢复。测试包含同世界正常重开、跨 attempt 阻断、错误身份/摘要拒绝、授权切换回滚、撤销库存破坏拒绝、真实 CPU 搅拌减速和旧证明不停止其他作业；旧几何/多样品路径按原协议回归。

这是设施端兼容，不是完整 OS 恢复交付。设备包尚未消费并持久保存新停止证明，UNKNOWN 不自动释放；ROS、真实 PLC/Isaac 与新的进程强制终止实验均未验证。生产 binding v1/v2 没有绑定 sample_context，当前明确拒绝该组合，生产多样品适配仍待完成；旧多样品协议继续保留。
