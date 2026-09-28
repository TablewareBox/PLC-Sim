# CPU参考执行授权账本

`plc_sim.reference_execution_authority.Authority` 提供设施签发的授权凭证摘要、代次、撤销和操作绑定记录。它与已有 `ReferenceOperationGate` 的设备租约不同：执行者自行更换executor_id不能恢复已撤销的授权。当前两模块尚未在正式设施中组合，不存在导入后自动升级授权的行为。

创建时传入调用方持有的SQLite连接和至少32字符的管理员凭证，`create=True`逐条创建原有七张表。保留旧 `labos.plate-authority/v1` schema及表结构，只迁移实现归属；`canonical/identifier`复用已在SDK中的相同函数。该模块不依赖OPC UA、OS、设备包或旧work目录；兼容完整包名和现有源码目录导入。

宿主必须先取得设施/世界锁，再在一个SQLite事务内依次调用 `validate_transition(request, world_id)`、`apply(safe)` 和 `record_transition(safe, response)`。已存在的相同transition返回原回执，但不得重新apply；不同内容复用transition_id被拒绝。代次比较本身不加锁；分开调用或并发绕过宿主锁不具有原子性保证。

管理员凭证只用于inspect/activate/revoke，不能直接冒充执行授权；`authenticate(token)`只接受当前active凭证。原凭证和grant_id不可复用；重放旧激活回执不会重新激活已撤销授权。数据库保存凭证摘要，`checkpoint()`给出脱敏摘要与关联清单。构造器核对schema和管理员摘要，但不会独自验证完整历史封存或外部身份真伪。

`record_operation`记录宿主提供的执行者、设备租约epoch和origin身份；它不会代替OS身份验证。`apply`和记录函数只接受内部已验证调用，不能直接暴露成网络API。宿主负责请求校验、事务失败处理、实际控制器停止、世界状态持久化和授权/资源记录的一致性。撤销账本并不代表硬件或模拟设备已经停稳。

模块不自行commit、创建/关闭连接、派发设备、推进世界或开线程；`time.time()`沿用审计时间戳，不是联合仿真时钟。具体板/样品孔组预约及设备动作准入仍在Uni-Lab-Instruments，不迁入SDK。

验证：原冻结宿主及外部仅替换本模块后各17项检查通过，包含并发代次冲突、旧授权拒绝和临时子进程SIGKILL事务边界。SDK内22项直接账本检查、原操作门控8项检查通过；包名/源码目录两种入口在禁用site的解释器中导入通过。来源见[迁移清单](../migration/reference-execution-authority.json)。临时旧宿主仍有未迁入的恢复/停止实现，不能将这些回归算作当前正式组合的能力。

不修改既有启动JSON、顶层simulation或OS解析，没有构建、安装、运行硬件或切换服务。本模块仅用于CPU参考设施；当前原生OS、ROS、Isaac、PLC程序及生产授权均未由本批验收。
