# 物料托管的可选几何检查边界

`material_world.World`恢复冻结来源的可选 `_operation_geometry_guard` 回调，默认None。由宿主在装配后、执行前显式安装设备包的检查器；SDK不加载领域合同或场景，不根据配置自动启用。本批不修改启动JSON、顶层simulation、OS解析或服务。

`claim_plate`和`release_plate`保持World为托管事实源。在同一世界锁内，先做原托管状态检查，再调用`handoff`；允许后才改owner并发出原`plate.claimed/released`事件；最后以原事件sequence调用`handoff_committed`。回调接收`(boundary, details)`，拒绝须抛异常；返回值沿用旧合同不消费。未安装回调时，原事件与托管行为保持不变。

前置回调拒绝时尚未改变托管或发模型事件；后置回调失败时变化已经发生，World不伪装回滚。持久事务、设备状态恢复、故障隔离和完整审计由宿主负责。`check_operation_geometry`本身不独立加锁，直接调用者须持有同一世界锁及相应外层事务。回调可在重入世界锁下读取事实，不应推进时钟或重新派发动作。

来源World整类AST保持一致。SDK原21项检查及新增8项边界检查通过，覆盖默认行为、前置拒绝、前后实际owner、事件序号、后置错误保留效果和并发争用。当前仪器包48项相关检查在提供固定原驱动只读夹具后无跳过；首轮未指定夹具的1项跳过独立保留，不计通过。详见[迁移清单](../migration/material-world-geometry-hooks.json)。

设备包GeometryRuntime已有领域判据，PRCXI实际步骤钩子仍待下一笔迁移；当前正式设施没有自动安装guard。这一物料托管边界不证明3D路径、碰撞、硬件动作或PLC反馈，亦未提供统一仿真时钟或世界恢复。
