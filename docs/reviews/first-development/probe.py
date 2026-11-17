"""首轮只读装配探针；不合并分支，不修改模块，不代表生产适配器。"""
import argparse
import asyncio
import base64
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    for key in ('a', 'b', 'c', 'd'):
        parser.add_argument('--' + key, type=Path, required=True, help='该模块所在仓库根目录')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.a / 'modules/desktop-world/src'))
    sys.path.insert(0, str(args.c / 'modules/interaction/src'))
    from desktop_world.model import Package, PackageError, World
    from engine import Interaction
    from providers import FixedModel, FixtureCapture

    report = {}
    package_path = args.b / 'modules/character-studio/fixtures/sample.character.json'
    raw = json.loads(package_path.read_text(encoding='utf-8'))
    try:
        Package.load(package_path)
        report['B_to_A_direct'] = 'unexpectedly accepted'
    except PackageError as exc:
        report['B_to_A_direct'] = str(exc)

    # 仅证明样例的最小数据转换可行；缺失身体语义采用显式假设，不能作为正式转换规则。
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        actions = {}
        for action in raw['actions']:
            frames = []
            for index, frame in enumerate(action['frames']):
                data = base64.b64decode(raw['assets'][frame['asset']].split(',', 1)[1])
                w, h = struct.unpack('>II', data[16:24])
                contact = action['contacts'][0]
                name = f'{action["id"]}-{index}.png'
                (root / name).write_bytes(data)
                frames.append(dict(file=name, durationMs=int(frame['durationMs']),
                                   contact=[contact['x'] * w, contact['y'] * h]))
            actions[action['id']] = dict(frames=frames, loop=action['loop'], priority=0,
                states=['standing', 'lying', 'clinging', 'hanging', 'falling', 'dragging'],
                contactKind='feet')
        manifest = root / 'character.json'
        manifest.write_text(json.dumps(dict(schema='A-display/1', id=raw['character']['id'],
            version=str(raw['revision']), name=raw['character']['name'], actions=actions)), encoding='utf-8')
        package = Package.load(manifest)
        world = World()
        world.add('probe-instance', package, 100, 100)
        world.tick(100)
        report['B_to_A_explicit_sample_conversion'] = 'loaded and ticked; feet-only assumed'
        world.dispose()

    roles = json.loads((args.c / 'modules/interaction/fixtures/roles.json').read_text(encoding='utf-8'))
    engine = Interaction(roles, FixedModel(), FixtureCapture())
    try:
        try:
            engine.submit('', image='share.png', kind='image')
            report['D_image_only_to_C'] = 'accepted'
        except ValueError as exc:
            report['D_image_only_to_C'] = str(exc)
        engine.submit('装配探针')
        for _ in range(200):
            if not engine.tasks:
                break
            await asyncio.sleep(.02)
        for status in ('queued', 'interrupted', 'failed'):
            try:
                engine.feedback(engine.actions[0]['id'], status)
                report['C_feedback_' + status] = 'accepted'
            except ValueError as exc:
                report['C_feedback_' + status] = str(exc)
        world = World()
        a_package = Package.load(args.a / 'modules/desktop-world/fixtures/moss/character.json')
        for role in roles:
            world.add(role['id'], a_package, 100, 100)
            world.dock(role['id'])
        results = []
        world.subscribe(lambda event: results.append(event) if event['type'] == 'action-result' else None)
        for action in engine.actions:
            world.command(action['role'], action['action'], action['id'])
        world.tick(800)
        report['C_to_A_sample_action_results'] = results
        for event in results:
            engine.feedback(event['requestId'], event['status'])
        report['C_to_A_to_C_mapped_feedback'] = [a['status'] for a in engine.actions]
        world.dispose()
        snap = engine.snapshot()
        mapped_roles = [dict(id=r['id'], visible=True, interactive=True, x=200+i*300, y=500)
                        for i, r in enumerate(roles)]
        script = r'''import {pathToFileURL} from 'node:url';
const {layoutBubbles} = await import(pathToFileURL(process.argv[1]).href);
const p=JSON.parse(process.argv[2]);const now=Date.now();
const direct=layoutBubbles(p.messages,p.roles,{width:1200,height:700},now,false).length;
const mapped=p.messages.map(m=>({...m,speakerId:m.sender,at:now}));
const adapted=layoutBubbles(mapped,p.roles,{width:1200,height:700},now,false).length;
console.log(JSON.stringify({direct,adapted}));'''
        result = subprocess.run(['node', '--input-type=module', '-e', script,
            str(args.d / 'modules/companion-ui/src/model.js'),
            json.dumps(dict(messages=snap['messages'], roles=mapped_roles))],
            capture_output=True, text=True, check=True)
        report['C_to_D_bubbles'] = json.loads(result.stdout)
        report['assembly_code_present'] = sorted(str(p.relative_to(args.a)) for p in
            (args.a / 'apps/desktop').rglob('*') if p.is_file())
    finally:
        await engine.close()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + '\n', encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    asyncio.run(main())
