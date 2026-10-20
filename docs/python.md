# Python 库

包名和导入名都是 `oniharness`。使用 **uv + Python 3.15** 开发，
对外支持 Python 3.10 及以上。Python 包和 .NET CLI/Core 分别发布、分别编号。

## 安装

```sh
uv python install 3.15
uv venv --python 3.15
uv pip install oniharness
```

已有 uv 项目可以直接 `uv add oniharness`。从源码开发：

```sh
git clone https://github.com/LIghtJUNction/OniHarness.git
cd OniHarness
uv sync --python 3.15
uv run python -m unittest discover -s python/tests -v
```

## 用装饰器写测试

配置一次，登记测试，最后 `suite.run()`。导入文件、应用装饰器时不会构建后端或运行物理测试。
下面的例子在三种温度下各运行三次，共九个独立 worker：

```python
from oniharness import Cell, Scenario, Suite, expect, parametrize

suite = Suite(
    checkout="/path/to/OniHarness", build=True,
    game_path="/path/to/OxygenNotIncluded",
)

@suite.scenario(repeat=3)
@parametrize(temperature=[280, 300, 320])
@expect.mass_conserved()
@expect.cell_temperature(8, 8, min=270, max=330)
def oxygen(temperature):
    return Scenario(fill=Cell("Oxygen", temperature=temperature))

print(suite.run(report_path="results/oxygen.json"))
# PASS: 9/9 tests passed
```

- `@suite.scenario` 可以裸用，也可以指定 `name`、`repeat`、`timeout`。
- `@parametrize(temperature=[280, 300], mass=[1, 2])` 产生四种组合；叠加多个装饰器也是组合相乘。
- `@expect.mass_conserved(tolerance=0.01)` 检查质量守恒。
- `@expect.total_mass(min=..., max=...)`、`@expect.cell_mass(x, y, ...)`、
  `@expect.cell_temperature(x, y, ...)` 检查最终值范围，至少提供一个边界。

装饰器的上下顺序可以互换，原函数仍然是普通的场景工厂，签名和文档保留。
工厂只应构造并返回 `Scenario` 或原有 JSON 字典；定义阶段不是模拟执行阶段。
`Suite` 用函数名命名用例，`@suite.scenario(name="...")` 可以覆盖；参数自动追加到名字中。
`suite.run(filter="temperature=300")` 只准备、运行匹配的用例。空筛选结果、重复名字、
参数不匹配或错误返回值会抛出带用例名的 `DefinitionError`，在任何原生测试开始前停止。
不支持异步或生成器场景工厂。

`Scenario`、`Cell`、`Region` 是不可变的输入对象，使用 Kelvin、kg 和格坐标。
后端负责验证完整物理规则。断言也可以直接附在场景上：

```python
from oniharness import Cell, Region, Scenario, expect

base = Scenario(
    fill=Cell("Vacuum", mass=0),
    regions=(Region(2, 3, width=4, height=2, cell=Cell("Water", mass=1000)),),
)
checked = base.expect(expect.mass_conserved())  # 返回新场景，不修改 base
```

可以 `Suite(existing_harness)` 复用已配置的 `Harness`；也可以 `Suite()` 使用 PATH 上的 CLI。
`Suite` 构建后端和各个 worker 的规则与原有接口相同。运行时测试失败会继续收集后续用例，
保存报告后再抛出 `TestFailure`；`check=False` 返回失败报告供检查。配置/命令错误仍会中止。
`report.save(path)` 原子写入 JSON；`print(report)` 显示通过数，原始证据仍在 `report.tests`。

完整脚本：[decorated_scenarios.py](../examples/python/decorated_scenarios.py)：

```sh
uv run --python 3.15 python examples/python/decorated_scenarios.py \
  --build --game-path /path/to/OxygenNotIncluded
```

## 原有底层接口

需要 .NET SDK 10、OniHarness 源码或单独安装的 CLI，以及本机正版游戏文件。
wheel 不包含 .NET 后端和游戏文件。首次从源码构建后端用 `build=True`；
以后可省略。`from_checkout` 的路径必须是可信源码，因为构建会执行其构建配置。

```python
from oniharness import Harness, TestFailure

harness = Harness.from_checkout(
    "/path/to/OniHarness",
    build=True,
    game_path="/path/to/OxygenNotIncluded",
)
print(harness.doctor())

try:
    report = harness.run_scenario({
        "name": "密闭氧气质量守恒",
        "width": 16, "height": 16, "steps": 50,
        "fill": {"element": "Oxygen", "mass": 1, "temperature": 300},
        "assertions": [{"kind": "massConserved", "absoluteTolerance": 0.01}],
    }, repeat=3, report_path="results/oxygen.json")
except TestFailure as error:
    for test in error.report.tests:
        print(test["status"], test["error"])
    raise
print(report.passed)
```

也可以 `Harness(game_path=...)` 使用 PATH 上的 `oniharness`，
或 `Harness(command=["/path/to/oniharness"], game_path=...)` 指定命令。
游戏路径未指定时，由 CLI 读取 `ONIHARNESS_GAME_PATH`。

```python
report = harness.test_scenarios("examples/scenarios", repeat=3)
report = harness.test_assembly("MyMod.Tests.dll", filter="MyMaterial", timeout=30)
contract = harness.contract(
    "/path/to/Assembly-CSharp.dll",
    type_name="Sim", method="GetHandleIndex", parameters=1,
)
print(contract["found"])  # 仅元数据存在性，不代表执行成功
```

`timeout` 是单个 worker 的秒数，1..600。`repeat` 是 1..100。
`Harness(process_timeout=300)` 默认限制整次 CLI 调用为 300 秒，
大量场景可显式增大；到期会终止 CLI 和其子进程。
`TestReport.data` 保留原始 JSON，`.tests` 保留证据类型、哈希、步数和失败原因。
`check=False` 可获取失败报告；空结果不会变成成功。
`CommandError` 表示命令/配置失败，`HarnessError` 表示后端缺失、进程超时或无效报告。

原生适配器目前只验证了 Linux x64、744825 构建的指定文件哈希。
错误版本不会执行；不要替换 DLL 绕过检查。原生场景不运行复制人、建筑生命周期、
完整存档或 Unity。测试通过只说明该场景的实际测量通过。

完整脚本：[native_scenario.py](../examples/python/native_scenario.py)。

## 读取运行中的游戏

需要已启用 OniMcp 的游戏；启动和无头运行参见 [runtime.md](runtime.md)。
这里不自动启动或恢复游戏，也不加载存档。

```python
import os
from oniharness import Game, McpClient

client = McpClient(
    "http://127.0.0.1:8788/mcp/",
    token=os.environ.get("ONIHARNESS_MCP_TOKEN"),
)
print([tool["name"] for tool in client.list_tools()])
observation = Game(client).observe()
print(observation["map"]["text"])
print(observation["dupes"])
```

`observe()` 先暂停并读回确认，再读取摄像机附近的小范围原始文本地图。
可以 `observe(bounds=(0, 0, 31, 23))` 指定最多 1600 格的区域。
坐标必须来自当前世界的实际观测，不要照搬示例去操作未知地形。
返回后游戏保持暂停；文本地图来自 OniMcp，不是截图。

完整脚本：[observe_game.py](../examples/python/observe_game.py)。
两份脚本均通过 `--help` 查看参数，用 `uv run python examples/python/文件名.py ...` 运行。

`McpClient.call_tool(name, arguments)` 可显式调用当前工具。
这个底层接口也能修改游戏，不自动暂停或替你确认危险操作。
遵守暂停、观察、计划、执行、短时运行、暂停、验证的流程。
客户端只允许 HTTP 回环地址，禁用代理和重定向；支持 JSON、SSE 和 TOON。
工具或协议失败抛出 `McpError`。不要并发共享同一个客户端。

### 暂停装饰器

`Game()` 可以直接连接默认地址，自动读取 `ONIHARNESS_MCP_TOKEN`。
自定义地址仍可传 `Game(McpClient(url, ...))`。

```python
from oniharness import Game

game = Game()

@game.paused
def colony_summary():
    return game.client.call_tool(
        "world_editor", {"command": "read", "path": "/active/index.md"},
    )

print(colony_summary())
```

运行函数前暂停并读回确认；成功返回或抛出异常时，都会再次尝试暂停并确认。
第一次暂停失败不会执行函数体，最后暂停失败也不会报告成功。
保留返回值、签名和文档，不自动恢复游戏。函数必须同步执行，不能返回待执行的协程或生成器。
装饰器不授权危险写入，也不替代有停止保护的短步运行流程。

## 构建与发布

```sh
uv sync --python 3.15 --locked
uv run python -m unittest discover -s python/tests -v
uv run mypy --python-version 3.10 python/src/oniharness
uv build --out-dir artifacts/python
uv run twine check artifacts/python/*
uv run python scripts/verify_python_distribution.py artifacts/python
uv publish -q artifacts/python/*
```

发布需要 PyPI token、uv 已保存的凭据或配置好的 Trusted Publisher。
`-q` 只减少输出，不跳过认证。不要把 token 写进源码、日志或示例。
可用 GitHub Actions 的 `python-publish.yml` 发布：PyPI 配置对应仓库、
工作流和 `pypi` environment 的 Trusted Publisher 后，推送 `python-v*` 标签。
发布工作流只接受与 pyproject 版本匹配的标签，并验证 wheel/sdist 再上传。
