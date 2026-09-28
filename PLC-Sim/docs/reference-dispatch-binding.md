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


## C37d：区分运行时节点与设施设备（0.2.8）

既有孔板启动图中的 OS 节点 `plate_prcxi` 对应设施设备 `prcxi`，另外两台同样有前缀。
v1 要求两者同名，因此不能直接消费该图；原测试的同名夹具没有覆盖此边界。

调用 `bind_dispatch(..., target={"local_device_id": ..., "device_id": ...})` 生成
`labos.production-dispatch-binding/v2`。两个身份均保留：target.local_device_id 必须等于
dispatch.local_device_id，target.device_id 必须等于实际 operation.device_id；操作摘要仍
独立重算，完整 target 进入原操作的身份去重。缺失映射、错配、额外字段和版本降级均拒绝。
未提供 target 时仍生成原 v1，并保持其同名约束。旧五字段身份也保持原义。

节点映射由持有设施凭据的调用方声明；门控验证内部一致性，不查询 ROS 注册表、OS claim
或证明该映射获得授权。设备包必须先核对实际包装器与驱动的关联。world、token、租约、
期限、动作白名单、数据库表及去重事务均保持，映射不能替代权限或跨世界隔离。

源码版本为0.2.8，未构建/安装。新增15项纯合同检查；包含原协议、驱动和原图包装器路径的
跨仓162项检查通过。ROS叶子由替身提供，未运行ROS/GPU，也未接通UNKNOWN持久恢复。
