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

## 不启动游戏，运行物理场景

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

## 构建与发布

```sh
uv sync --python 3.15 --locked
uv run python -m unittest discover -s python/tests -v
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
