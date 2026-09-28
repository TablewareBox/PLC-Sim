# 生产派发身份的参考设施绑定

`reference_dispatch_binding.bind_dispatch(context, parameters=..., operation=...)` 生成
`labos.production-dispatch-binding/v1`：完整 OS 公开执行身份快照、实际调用参数摘要、
五字段操作摘要。`validate_dispatch_binding` 可核对完整字段、类型、目标设备及操作摘要。
该模块没有数据库、网络、时钟、设备行为或令牌，不创建身份或执行许可。

`ReferenceOperationGate` 同时识别原五字段实验身份和新版本；未知版本、缺失字段、
操作替换与目标错配均在效果前拒绝。原 dispatcher_token、executor_id/epoch、期限、
world、动作白名单以及 operation_id 查重继续生效。去重记录保留完整身份，不同
job/claim/attempt/fence 不能取代同一 operation_id 的已受理身份。两次参数文本不同，
即使解析后操作相同，也不能借同一 operation_id 静默更换原调用的参数摘要。

源参数摘要由持有设施凭据的调用方声明；接收端仅独立重算操作摘要。两者都不是签名，
该接口不查询 OS claim 当前有效性。OS 实际开始屏障与设施自身租约分别承担各自授权，
不能把 claim/fence 数值当作设备 executor/epoch。旧授权账本仍含 origin_instance_id，
恢复设施没有因此获得新身份支持；不能把 claim_uuid 重命名为 origin_instance_id。

PLC-Sim 源码版本调整至0.2.7，设备包要求该接口版本；本批没有构建、安装或发布包。
原表结构、授权账本、恢复协调器和状态保存未改。新世界可使用新绑定；本批不打开或
迁移现有世界/运行数据库。18项纯合同检查及跨仓真实CPU回环检查通过，详见Testbed
C37c回执；这些不证明厂家PLC程序、ROS或Isaac联合运行。
