#!/usr/bin/env python3
"""Run an isolated native physics experiment and create a self-contained state replay."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def html_replay(trace, summary, destination):
    payload = json.dumps({"trace": trace, "summary": summary}, ensure_ascii=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>OniHarness 物理实验回放</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#111827;color:#eef2ff;font:16px system-ui,sans-serif}
main{max-width:1000px;margin:36px auto;padding:0 24px}h1{font-size:28px;margin:0 0 12px}p{color:#aebbd0;line-height:1.7}
.controls,.metrics{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:22px 0}button{background:#a7f3d0;color:#10281e;border:0;padding:10px 18px;border-radius:8px;cursor:pointer}
input{flex:1;min-width:180px;accent-color:#6ee7b7}.metrics>div{flex:1;min-width:150px;background:#1f2937;padding:12px;border-radius:8px}.metrics small{display:block;color:#aebbd0;margin-bottom:6px}
.views{display:grid;grid-template-columns:1fr 1fr;gap:22px}canvas{width:100%;image-rendering:pixelated;background:#080d18;border:1px solid #334155;border-radius:8px}h2{font-size:16px}.good{color:#6ee7b7}.bad{color:#fca5a5}
#cell,#event{min-height:28px;color:#cbd5e1}li{margin:9px 0}a{color:#93c5fd}@media(max-width:680px){.views{grid-template-columns:1fr}main{padding:0 14px}}
</style><main><h1>OniHarness 物理实验回放</h1>
<p>真实原生模拟库的状态记录。没有启动游戏；这是物理测试场景，未包含复制人和建筑。</p>
<div id="status"></div><div class="controls"><button id="play">播放回放</button><input id="frame" type="range" min="0" value="0" aria-label="模拟帧"><span id="time"></span></div>
<div id="event"></div><div class="views"><section><h2>水色明暗：0–1000 kg/格；灰色为岩石</h2><canvas id="material" width="384" height="384"></canvas></section>
<section><h2>温度：蓝色 300 K → 红色 450 K</h2><canvas id="temperature" width="384" height="384"></canvas></section></div><p id="cell">移到格子上可查看坐标、质量和温度。</p>
<div class="metrics"><div><small>水的质量</small><span id="mass"></span></div><div><small>水的重心高度</small><span id="height"></span></div><div><small>右侧水量</small><span id="right"></span></div><div><small>水的质量加权均温</small><span id="heat"></span></div></div>
<h2>实际操作记录</h2><ul id="events"></ul><h2>实际检查结果</h2><ul id="checks"></ul><p>每次操作在记录的步数之后请求，效果从后续状态读取。水量低于 0.01 kg 的格子以空格显示，原始数值仍保留。相变、疾病和辐射在本测试适配器中关闭。</p>
<p id="provenance"></p><p>数据回放不能证明完整殖民地游玩、Harmony 补丁兼容或游戏 FPS。</p></main>
<script id="data" type="application/json">__PAYLOAD__</script><script>
const {trace:t,summary:s}=JSON.parse(document.getElementById('data').textContent), frames=t.frames;
const byId=id=>document.getElementById(id), slider=byId('frame'); slider.max=frames.length-1;
const names=new Map(t.elements.map(e=>[e.index,e.name]));
byId('status').className=s.passed?'good':'bad';byId('status').textContent=s.passed?'检查通过；独立重复结果一致':'存在检查失败或重复结果不一致，请查看 summary.json';
byId('provenance').textContent=`适配器 ${t.adapter} · ${t.stepsCompleted} 步 · ${s.runs.length} 次独立运行。完整数值保存在各运行目录的 trace.json。`;
for(const c of t.checks){const li=document.createElement('li');li.className=c.passed?'good':'bad';li.textContent=`${c.passed?'PASS':'FAIL'} ${c.name}：${c.observed.toFixed(4)}；预期 ${c.expected}`;byId('checks').append(li)}
for(const e of t.events){const li=document.createElement('li');li.textContent=`第 ${e.step} 步之后：${e.action}`;byId('events').append(li)}
function color(i,f,mode){const n=names.get(f.state.elements[i]);if(n==='Vacuum'||n==='Void'||(n==='Water'&&f.state.mass[i]<.01))return '#080d18';
if(mode==='temperature'){const q=Math.max(0,Math.min(1,(f.state.temperature[i]-300)/150));return `hsl(${220-220*q} 78% 55%)`}
if(n==='Unobtanium')return '#cbd5e1';if(n==='Granite')return '#64748b';if(n==='Water'){const a=Math.min(1,f.state.mass[i]/1000);return `rgb(${8+22*a} ${13+130*a} ${24+231*a})`}return '#a78bfa'}
function draw(){const f=frames[+slider.value];for(const mode of ['material','temperature']){const canvas=byId(mode),ctx=canvas.getContext('2d'),w=canvas.width/t.width,h=canvas.height/t.height;
for(let y=0;y<t.height;y++)for(let x=0;x<t.width;x++){const i=y*t.width+x;ctx.fillStyle=color(i,f,mode);ctx.fillRect(x*w,(t.height-1-y)*h,w,h);ctx.strokeStyle='#111827';ctx.strokeRect(x*w,(t.height-1-y)*h,w,h)}}
byId('time').textContent=`${f.seconds.toFixed(1)} s · 第 ${f.step} 步`;byId('mass').textContent=f.waterMass.toFixed(2)+' kg';byId('height').textContent=f.waterCentreY.toFixed(3)+' 格';byId('right').textContent=f.rightWaterMass.toFixed(2)+' kg';byId('heat').textContent=f.waterMeanTemperature.toFixed(3)+' K';
byId('event').textContent=({initial:'初始状态：水位于左侧高处',falling:'观察：水在重力下落入左侧槽底','partition-open':'操作后观察：移除三个隔板格，水流向右侧',heated:'操作后观察：向一个实际水格加入 80000 kJ 能量'})[f.stage]||f.stage}
let timer=null;function pause(){clearInterval(timer);timer=null;byId('play').textContent='播放回放'}
byId('play').onclick=()=>{if(timer){pause();return}if(+slider.value>=frames.length-1)slider.value=0;byId('play').textContent='暂停';timer=setInterval(()=>{slider.value=+slider.value+1;draw();if(+slider.value>=frames.length-1)pause()},150)};
slider.oninput=()=>{pause();draw()};for(const mode of ['material','temperature'])byId(mode).onpointermove=e=>{const rect=e.currentTarget.getBoundingClientRect(),x=Math.min(t.width-1,Math.floor((e.clientX-rect.left)*t.width/rect.width)),y=t.height-1-Math.min(t.height-1,Math.floor((e.clientY-rect.top)*t.height/rect.height)),i=y*t.width+x,f=frames[+slider.value];byId('cell').textContent=`(${x}, ${y}) ${names.get(f.state.elements[i])} · ${f.state.mass[i].toFixed(3)} kg · ${f.state.temperature[i].toFixed(3)} K`};draw();
</script></html>'''
    destination.write_text(page.replace("__PAYLOAD__", payload), encoding="utf-8")


def image_replay(trace, summary, directory):
    from PIL import Image, ImageDraw, ImageFont
    import colorsys

    font = ImageFont.load_default(size=16)
    small = ImageFont.load_default(size=13)
    names = {e["index"]: e["name"] for e in trace["elements"]}
    tile = 16
    board_w, board_h = trace["width"] * tile, trace["height"] * tile

    def frame_image(frame):
        image = Image.new("RGB", (board_w * 2 + 72, board_h + 170), "#111827")
        draw = ImageDraw.Draw(image)
        draw.text((24, 14), "OniHarness - real native simulation data replay", font=font, fill="#eef2ff")
        status = "PASS" if summary["passed"] else "FAIL"
        draw.text((24, 40), f'{status} | {frame["seconds"]:.1f} simulated seconds | step {frame["step"]} | {frame["stage"]}', font=small, fill="#a7f3d0" if summary["passed"] else "#fca5a5")
        draw.text((24, 66), "Water brightness: 0-1000 kg/cell", font=small, fill="white")
        draw.text((board_w + 48, 66), "Temperature (blue 300 K - red 450 K)", font=small, fill="white")
        state = frame["state"]
        for y in range(trace["height"]):
            for x in range(trace["width"]):
                i = y * trace["width"] + x
                name = names[state["elements"][i]]
                material = {"Vacuum": "#080d18", "Void": "#080d18", "Unobtanium": "#cbd5e1", "Granite": "#64748b"}.get(name, "#1e90ff")
                empty = name in {"Vacuum", "Void"} or (name == "Water" and state["mass"][i] < .01)
                if name == "Water":
                    alpha = min(1, state["mass"][i] / 1000)
                    material = tuple(int(a + b * alpha) for a, b in [(8, 22), (13, 130), (24, 231)])
                if empty:
                    material = "#080d18"
                q = max(0, min(1, (state["temperature"][i] - 300) / 150))
                rgb = tuple(int(v * 255) for v in colorsys.hsv_to_rgb((220 - 220 * q) / 360, 0.78, 0.95))
                temperature = "#080d18" if empty else rgb
                for origin, color in [(24, material), (board_w + 48, temperature)]:
                    left, top = origin + x * tile, 90 + (trace["height"] - 1 - y) * tile
                    draw.rectangle((left, top, left + tile - 1, top + tile - 1), fill=color, outline="#111827")
        draw.text((24, board_h + 105), f'Water: {frame["waterMass"]:.2f} kg | height: {frame["waterCentreY"]:.3f} cells | right chamber: {frame["rightWaterMass"]:.2f} kg', font=small, fill="#e2e8f0")
        draw.text((24, board_h + 130), f'Mean water: {frame["waterMeanTemperature"]:.3f} K | <0.01 kg hidden | fixture, not colony gameplay', font=small, fill="#e2e8f0")
        return image

    images = [frame_image(frame).quantize(colors=128) for frame in trace["frames"]]
    images[0].save(directory / "replay.gif", save_all=True, append_images=images[1:], duration=150, loop=0)
    event_steps = [event["step"] for event in trace["events"]]
    selected = [0, *[next(i for i, frame in enumerate(trace["frames"]) if frame["step"] == step) for step in event_steps], len(images) - 1]
    width, height = images[0].size
    overview = Image.new("RGB", (width, height * len(selected)), "#111827")
    for row, index in enumerate(selected):
        overview.paste(images[index], (0, row * height))
    overview.save(directory / "overview.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-path", default=os.environ.get("ONIHARNESS_GAME_PATH"))
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/physics-playground")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--phase-step-limit", type=int, default=5000, help="Maximum steps per observation phase; multiple of 20")
    parser.add_argument("--gif", action="store_true", help="Also render GIF/PNG using an already installed Pillow")
    args = parser.parse_args()
    if not args.game_path:
        parser.error("Provide --game-path or ONIHARNESS_GAME_PATH")
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat must be 1..10")
    if not 20 <= args.phase_step_limit <= 5000 or args.phase_step_limit % 20:
        parser.error("--phase-step-limit must be a multiple of 20, between 20 and 5000")
    if args.gif:
        try:
            import PIL  # noqa: F401
        except ImportError:
            parser.error("--gif needs Pillow; omit --gif for the dependency-free HTML replay")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(["dotnet", "build", "OniHarness.slnx", "-c", "Release", "--nologo"], cwd=ROOT, check=True)
    cli = ROOT / "src/OniHarness.Cli/bin/Release/net10.0/OniHarness.Cli.dll"
    assembly = ROOT / "examples/PhysicsPlayground/bin/Release/net10.0/PhysicsPlayground.dll"
    runs, traces = [], []
    for index in range(1, args.repeat + 1):
        directory = Path(tempfile.mkdtemp(prefix=f"run-{index:02d}-", dir=output))
        env = dict(os.environ, ONIHARNESS_PLAYGROUND_OUTPUT=str(directory), ONIHARNESS_PLAYGROUND_PHASE_LIMIT=str(args.phase_step_limit))
        process = subprocess.run(["dotnet", str(cli), "test", "--assembly", str(assembly), "--game-path", args.game_path,
                                  "--timeout", "20", "--report", str(directory / "result.json")], cwd=ROOT, env=env)
        trace_path = directory / "trace.json"
        trace = json.loads(trace_path.read_text()) if trace_path.exists() else None
        runs.append({"directory": directory.name, "exitCode": process.returncode,
                     "passed": process.returncode == 0 and trace is not None and trace["passed"],
                     "finalFingerprint": trace["finalFingerprint"] if trace else None})
        if trace:
            traces.append(trace)
            for check in trace["checks"]:
                print(f'{"PASS" if check["passed"] else "FAIL"}: {check["name"]}: {check["observed"]:.4f}; expected {check["expected"]}', flush=True)
    repeatable = len(traces) == args.repeat and len({t["finalFingerprint"] for t in traces}) == 1
    summary = {"schemaVersion": 1, "evidence": "native-sim-fixture", "runs": runs, "repeatable": repeatable,
               "passed": all(r["passed"] for r in runs) and repeatable}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if traces:
        html_replay(traces[0], summary, output / "index.html")
        if args.gif:
            image_replay(traces[0], summary, output)
    print(json.dumps(summary, indent=2))
    print(f"Replay: {output / 'index.html'}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
