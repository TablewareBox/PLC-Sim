# CPU参考执行者接管与恢复确认

`ReferenceRecoveryCoordinator`集中维护`claim(request)`与`reconcile(request)`的租约代次、执行者退役、操作隔离标记及停止证明。代码来自冻结e30255f原设施的四个方法；除了显式依赖替换，方法AST保持。`ContractError`复用SDK同一类型，原异常类AST相同。不创建数据库表、世界、设备、线程或额外时钟。

构造时传入现有SQLite连接、world/environment身份、设备ID集合、宿主共用的两个可重入锁、`controllers`和两个回调。`controllers.busy/state/interrupt`提供实际控制状态与停止/恢复账本更新；`authenticate(request)`负责当前凭证和拒绝记录，`save_durable()`在当前事务里保存世界及账本，启用授权时显式提供`authority_current()`。设备ID与三项控制回调没有固定板名称；具体设备动作继续归驱动包。

| 入口 | 保留的行为 |
|---|---|
| `claim` | 同执行者复用epoch；更换执行者永久退役旧ID，停止当前控制过程，再增加epoch及持久化；等待后再次核验授权和租约 |
| `reconcile(mode=inspect)` | 匹配world、操作指纹及原执行身份后观察；未见操作且无停止隔离标记不能伪造terminal |
| `reconcile(mode=ensure_stopped)` | 先固定操作隔离标记并撤销其origin；只停止该操作实际拥有的过程，持久化后有限等待；停止证明绑定epoch、环境实例和控制器代次 |

等待沿用原最多2秒墙钟观察窗口，由既有模型时钟产生减速；模块不推进模型时间、不强写busy或转速。仍忙时明确返回非terminal，不把停止命令或世界停钟当停稳。它不核实OS原生身份来源，公开请求真实性仍由宿主负责。

数据库须有原leases、retired_executors、operations、operation_fences、revoked_origins、controller_processes、commits及reconciliation_checks表和设备初始行。公开方法沿用原事务提交点，应从宿主入口调用，不嵌套在其他尚未提交事务中；宿主负责生命周期、拒绝已关闭设施及统一锁顺序。持久化异常会传播并回滚对应SQL事务，但已经执行的停止效果不会由本模块撤销；调用方须隔离并处理内存/持久状态差异，不能据此重放旧动作。

21项新接口检查配合8项原门控检查通过；测试用真实SQLite和显式控制器回调，包括任意pump/reader设备ID，未假装物理停止。C32as外部冻结设施已消费正式设备事实和状态日志，在此基线上替换claim/reconcile后55项授权、样品和恢复检查通过，包含真实CPU控制器及自建临时子进程崩溃。来源、依赖替换和原测试基线见[迁移清单](../migration/reference-recovery-coordinator.json)。

当前正式Testbed设施尚未启用完整恢复组合。本批没有修改启动JSON、顶层simulation、OS解析、旧work或服务，没有构建/运行ROS/GPU或操作硬件；不能计为生产接管、完整联合时序或Isaac验收。
