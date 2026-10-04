# PLC公共包命名空间与旧命令兼容

此增量只新增可安装的 `unilab-plc-sim` 分发包、`plc_sim` 模块及 `plc-sim` 命令。版本 `0.2.6` 对应明确锁定的兼容运行时 `unilab-opcua-sim==0.2.6`，不表示尚未迁入的公共时钟、契约、设备模型已经发布。

原 `OpcUaSim/` 源码、`opcua_sim` 导入、`opcua-sim` 命令及其版本保持不变。新CLI只委托原CLI；没有复制或双重映射设备/GUI模块。仅 `import plc_sim` 不加载旧产品；调用兼容命令才加载 `opcua_sim.cli`。

当前委托范围：默认GUI、`gui`、`server`、`szlab-handshake`（旧别名`handshake`）、可选的`ino`。行为、错误与退出码仍归原产品；`plc-sim --version`明确输出兼容运行时身份，`--help`标明下面显示的是旧CLI。未添加PTLC、Modbus或新仿真能力。

Python支持沿用原产品的 `>=3.11,<3.12`。从同一仓分别构建并安装 `OpcUaSim/` 与 `PLC-Sim/` 的wheel；后者显式依赖前者，不依赖源码路径或editable映射。旧GUI安装器/一键脚本本组不变，也不授予新桌面安装器资格。

来源映射见 `migration/package-identity.json`。后续公共模块按功能加入本目录；不经兼容CLI隐式重导出旧设备模块。
