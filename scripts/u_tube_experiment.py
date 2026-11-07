#!/usr/bin/env python3
"""Run U-Tube Communicating Vessels physics experiment and generate telemetry & HTML report."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/OniHarness.Cli/bin/Release/net10.0/OniHarness.Cli.dll"
ASSEMBLY = ROOT / "examples/PhysicsPlayground/bin/Release/net10.0/PhysicsPlayground.dll"


def generate_html_report(csv_path: Path, output_html: Path):
    rows = []
    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)

    labels = [r["TimeSeconds"] for r in rows]
    left_level = [r.get("Level_LeftWaterLevel", "0") for r in rows]
    right_level = [r.get("Level_RightWaterLevel", "0") for r in rows]
    left_mass = [r.get("Region_LeftVessel", "0") for r in rows]
    right_mass = [r.get("Region_RightVessel", "0") for r in rows]

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>OniHarness 连通器物理仿真实验报告</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body {{ background: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 24px; }}
    .container {{ max-width: 1000px; margin: 0 auto; }}
    h1 {{ color: #38bdf8; font-size: 24px; }}
    .card {{ background: #1e293b; padding: 20px; border-radius: 10px; margin-bottom: 20px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
    th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #334155; }}
    th {{ color: #94a3b8; }}
    .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; background: #10b981; color: white; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🧪 OniHarness 连通器 (U-Tube) 物理仿真与遥测报告</h1>
    <div class="card">
      <p><span class="badge">原生仿真已验证</span> <strong>物理原则：</strong>缺氧真实底层流体模拟 (libSim.so) 重力流与连通器静力学平衡。</p>
      <p>采样总步数：<strong>{len(rows)} 步</strong> | 验证指标：左右管液面差 ≤ 1 格，总质量严格守恒。</p>
    </div>

    <div class="card">
      <h2>📈 左右两室液位变化时序图 (Liquid Level / Communicating Vessels)</h2>
      <canvas id="levelChart" height="120"></canvas>
    </div>

    <div class="card">
      <h2>⚖️ 左右两室水质量迁移时序图 (Mass Equalization)</h2>
      <canvas id="massChart" height="120"></canvas>
    </div>

    <div class="card">
      <h2>📋 关键采样数据记录 (前 10 步与终态)</h2>
      <table>
        <thead>
          <tr>
            <th>步数 (Step)</th>
            <th>仿真时间 (s)</th>
            <th>左室水位 (Y)</th>
            <th>右室水位 (Y)</th>
            <th>左室水量 (kg)</th>
            <th>右室水量 (kg)</th>
            <th>总质量 (kg)</th>
          </tr>
        </thead>
        <tbody>
          {''.join(f'<tr><td>{r["Step"]}</td><td>{r["TimeSeconds"]}</td><td>{r.get("Level_LeftWaterLevel", "-")}</td><td>{r.get("Level_RightWaterLevel", "-")}</td><td>{float(r.get("Region_LeftVessel", 0)):.1f}</td><td>{float(r.get("Region_RightVessel", 0)):.1f}</td><td>{float(r["TotalMass"]):.1f}</td></tr>' for r in (rows[:8] + ([rows[-1]] if len(rows) > 8 else [])))}
        </tbody>
      </table>
    </div>
  </div>

  <script>
    const labels = {json.dumps(labels)};
    const leftLevels = {json.dumps(left_level)};
    const rightLevels = {json.dumps(right_level)};
    const leftMass = {json.dumps(left_mass)};
    const rightMass = {json.dumps(right_mass)};

    new Chart(document.getElementById('levelChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: '左室水位高度 (Left Level)', data: leftLevels, borderColor: '#38bdf8', tension: 0.2 }},
          {{ label: '右室水位高度 (Right Level)', data: rightLevels, borderColor: '#34d399', tension: 0.2 }}
        ]
      }},
      options: {{ responsive: true, scales: {{ x: {{ title: {{ display: true, text: '时间 (秒)' }} }}, y: {{ title: {{ display: true, text: '网格高度 Y' }} }} }} }}
    }});

    new Chart(document.getElementById('massChart'), {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [
          {{ label: '左室总水质量 (kg)', data: leftMass, borderColor: '#60a5fa', tension: 0.2 }},
          {{ label: '右室总水质量 (kg)', data: rightMass, borderColor: '#f43f5e', tension: 0.2 }}
        ]
      }},
      options: {{ responsive: true, scales: {{ x: {{ title: {{ display: true, text: '时间 (秒)' }} }}, y: {{ title: {{ display: true, text: '质量 (kg)' }} }} }} }}
    }});
  </script>
</body>
</html>
"""
    output_html.write_text(html, encoding="utf-8")
    print(f"[+] HTML 连通器实验看板已生成：{output_html}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-path", default=os.environ.get("ONIHARNESS_GAME_PATH"),
                        help="Path to Oxygen Not Included installation")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/u-tube-experiment",
                        help="Output directory for telemetry and reports")
    args = parser.parse_args()

    if not args.game_path:
        parser.error("Provide --game-path or set ONIHARNESS_GAME_PATH")

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[*] 编译 OniHarness 测试项目...")
    subprocess.run(["dotnet", "build", str(ROOT / "OniHarness.slnx"), "-c", "Release", "--nologo"], check=True)

    env = dict(os.environ, ONIHARNESS_UTUBE_OUTPUT=str(output_dir))
    cmd = [
        "dotnet", str(CLI), "test",
        "--assembly", str(ASSEMBLY),
        "--game-path", args.game_path,
        "--report", str(output_dir / "report.json")
    ]

    print("[*] 正在执行连通器物理仿真实验并采集遥测数据...")
    proc = subprocess.run(cmd, cwd=ROOT, env=env, check=True)

    csv_file = output_dir / "communicating_vessels_data.csv"
    if csv_file.exists():
        generate_html_report(csv_file, output_dir / "index.html")

    print(f"[+] 实验圆满完成！数据文件保存在：{output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
