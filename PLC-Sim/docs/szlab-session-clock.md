# SZLab CLI 会话时间

`szlab-handshake serve` 的扫描与事件记录现在共用 `PackageSimulationRuntime.clock`。
CLI 将其 `now()` 传给已有握手模型，并将模型内部倍率设为 1：动作参数和配置延时以仿真秒
运行，倍率只在会话时钟中计算一次。直接使用设备包 `step(now)` / `time_scale` 的调用者不变。

倍率仍由原有 CLI/配置选择；没有新增场景格式或启动 JSON 字段。输出状态新增
`protocol.time_domain = "simulation_seconds"`，标识协议周期和事件 detail 的
`duration_seconds` 为仿真秒。以 4 倍速运行 2 秒动作时，该诊断值为 2，墙钟到期时间为
0.5 秒（实际还受轮询周期影响），不再显示缩短后的 0.5 秒作为动作时长。

12 个有限 CLI 用例使用真实握手模型、会话运行时、事件观察器与原子状态输出，通信适配器
替换为内存节点、墙钟由测试注入；覆盖整包/兼容模式、搅拌驱动时长/泵配置延时和三档倍率。
2 仿真秒分别在 0.25×/1×/4× 的 8/2/0.5 墙钟秒完成，事件 timestamp/completed_at 同为2。
原入口在全部12例被拒绝；相关既有回归合计119通过，1项可选Catalog环境检查跳过。

S1目前即时响应，回调仍由同一会话时钟记事件；其HTTP日志墙钟保留诊断用途，未模拟过程延迟。
本改动不冻结一个扫描内的所有时间读取，也没有实现物理步确认、暂停/单步、epoch或统一
PTLC调度。扫描sleep和网络超时保持墙钟，模型到期成功的旧语义保持，不能当作Isaac反馈。
来源与验证见[记录](../migration/szlab-session-clock.json)。
