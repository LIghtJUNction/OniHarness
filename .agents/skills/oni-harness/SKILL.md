---
name: oni-harness
description: Test ONI mods with game-free native fixtures or Steam-launched headless gameplay without a window or graphics device, plus arbitrary geometry, building placement, communicating vessels physics, telemetry data collection, and mod testing.
---

# OniHarness 测试技能

OniHarness 提供双重测试路线：
1. **独立原生物理仿真 (Game-Free Native Simulation)**：直接调用游戏底层 `libSim.so`，无需启动游戏或 Unity，测试任意几何地图、建筑物理映射、流体与热交换、数据采集；
2. **真实游戏无头运行 (Headless Game Runtime)**：通过 Steam 启动真实游戏（`-batchmode -nographics`），测试完整游戏生命周期与 Mod 集成。

---

## 快速反馈默认入口

每次迭代优先选择相关局部测试，复用构建产物。修改原生库时运行 `scripts/test_mod.py --native-project PATH --suite NAME` 或 `--filter PREFIX`，按文件选择使用 `--changed-file src/file.rs` 和项目内 `oniharness.json`。默认开发配置支持增量编译。只有需要更大覆盖时才扩大测试组。

工具自身的局部执行器变更使用 `python3 scripts/verify.py --only local`。报告分别给出构建、发现和执行时间，记录实际选中用例与产物复用状态。测试筛选不能静默漏掉未匹配名称。

托管入口必须指定 `--assembly`。`--skip-build` 用于复用明确选定的 DLL。`test_mod.py --headless` 当前会明确失败，原入口曾经只检查 DLL 就返回 runtimeVerified=true，该行为已修正。实机工作流只在用户授权需要实机时使用。

## 核心工作流与命令速查

所有命令统一在 `tools/OniHarness` 根目录下运行（或通过环境配置 `ONIHARNESS_GAME_PATH`）。

### 1. 连通器与物理实验 (Communicating Vessels & Physics Playground)

- **连通器实验与可视化数据看板生成**：
  ```sh
  python3 scripts/u_tube_experiment.py --game-path /path/to/OxygenNotIncluded
  ```
  自动运行连通器双室流体平衡测试，并将每步时序数据导出为 CSV/JSON，生成动态 HTML 报告：`artifacts/u-tube-experiment/index.html`。

- **连通器声明式 JSON 场景测试**：
  ```sh
  dotnet run --project src/OniHarness.Cli -c Release --no-build -- \
    test examples/scenarios/u-tube-communicating-vessels.json --game-path /path/to/OxygenNotIncluded
  ```

- **物理操场落水与加热实验**：
  ```sh
  python3 scripts/physics_playground.py --game-path /path/to/OxygenNotIncluded \
    --output artifacts/physics-playground --repeat 2
  ```

---

### 2. 安装 Mod 后测试 Mod (Mod Testing)

使用统一的 Mod 测试工具 `scripts/test_mod.py`：

```sh
# 自动编译 Mod，加载测试用例并验证托管逻辑
python3 scripts/test_mod.py --mod /path/to/MyMod \
  --assembly /path/to/MyMod.Tests.dll \
  --game-path /path/to/OxygenNotIncluded \
  --report artifacts/mod-test-report.json

# 托管局部筛选并复用已构建 DLL
python3 scripts/test_mod.py --mod /path/to/MyMod.dll \
  --assembly /path/to/MyMod.Tests.dll --filter RelevantCase --skip-build
```

在 C# 测试用例中动态加载 Mod 程序集：
```csharp
[HarnessTest]
public static void TestMyModLogic(HarnessContext context)
{
    var assembly = context.LoadModAssembly("/path/to/MyMod.dll");
    var targetType = assembly.GetType("MyMod.CustomBuildingConfig");
    Check.That(targetType != null, "Building config not found in mod.");
}
```

---

### 3. 任意形状地图与建筑物理模拟 (Arbitrary Shapes & Buildings)

在场景定义（JSON 或 C# `Scenario`）中支持多种复杂几何与建筑放置：

- **多边形区域 (`polygonRegions`)**：通过顶点坐标序列定义任意斜坡、多边形容器，底层内置光栅化检测：
  ```json
  "polygonRegions": [
    {
      "vertices": [ {"x": 4, "y": 2}, {"x": 20, "y": 2}, {"x": 18, "y": 5}, {"x": 6, "y": 5} ],
      "cell": { "element": "Granite", "mass": 1000, "temperature": 300 }
    }
  ]
  ```
- **离散点集区域 (`pointsRegions`)**：快速指定非连续障碍物点集：
  ```json
  "pointsRegions": [
    {
      "points": [ {"x": 10, "y": 6}, {"x": 12, "y": 6} ],
      "cell": { "element": "Granite", "mass": 1000, "temperature": 300 }
    }
  ]
  ```
- **ASCII 地图矩阵 (`asciiMap` & `legend`)**：直接用多行字符绘制二维网格拓扑。
- **建筑映射 (`buildings`)**：支持声明 `Tile`、`InsulatedTile`、`MeshTile`、`ManualPressureDoor` 等建筑，自动映射为底层物理阻隔与导热属性：
  ```json
  "buildings": [
    { "buildingDef": "InsulatedTile", "x": 6, "y": 4, "element": "Granite", "mass": 400, "temperature": 300 }
  ]
  ```

---

### 4. 数据采集与时序遥测 (SimulationDataCollector)

在 C# 物理测试中记录精细的时序数据：
```csharp
var collector = new SimulationDataCollector(scenario);
collector.AddProbe("LeftTubeBottom", 8, 4);       // 记录指定坐标的质量与温度
collector.MonitorColumn("LeftWaterLevel", 8);      // 记录指定列的最高液体高度
collector.MonitorRegion("LeftVessel", 6, 4, 5, 12); // 记录区域液体质量

for (int step = 0; step < 100; step += 5)
{
    world.Step(5);
    collector.Record(world.StepsCompleted, world.Snapshot());
}

// 导出 CSV 和 JSON 数据文件
collector.SaveCsv("artifacts/telemetry.csv");
collector.SaveJson("artifacts/telemetry.json");
```

---

### 5. 框架自身验证回归

```sh
# 框架无游戏依赖自检（13 项行为检查）
dotnet run --project tests/OniHarness.Tests -c Release --no-build

# Python 库单元测试（41 项检查）
PYTHONPATH=python/src .venv-runtime/bin/python -m unittest discover -s python/tests

# 运行全部内置场景测试
dotnet run --project src/OniHarness.Cli -c Release --no-build -- \
  test examples/scenarios --game-path /path/to/OxygenNotIncluded
```

---

## 实验结果判定原则

- 过程退出码为 0 且生成的测试报告 `status == "passed"`。
- 物理测试验证总质量守恒（`massConserved` 绝对容差 $\le 0.5\text{ kg}$）。
- 连通器实验验证双室自由液面齐平（`communicatingVesselsEquilibrium` 误差 $\le 1$ 格）。
- 遥测数据应包含平滑单调的质量转移时序，保留证据闭环文件（CSV/JSON）。

For repeat runs compare state fingerprints, not image timestamps or wall time.
Report native-solver evidence separately from .NET-host method execution and
static metadata. Qualify timings by fixture and steps; do not turn them into an
ONI FPS claim. Link the result JSON and replay, summarize measured changes, and
state that full colony/dupe gameplay was not exercised. Keep game files and
private saves out of commits and public artifacts.
