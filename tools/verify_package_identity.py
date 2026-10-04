"""核验已安装wheel的新旧包与命令；不从checkout导入、不启动服务。"""
from pathlib import Path
import argparse
import json
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('不得覆盖历史核验回执')
    interpreter = args.python.absolute()
    probe = '''
import importlib.metadata as metadata
import json,sys
from pathlib import Path
import plc_sim
assert 'opcua_sim' not in sys.modules
import opcua_sim
roots={name:Path(module.__file__).resolve() for name,module in [('plc_sim',plc_sim),('opcua_sim',opcua_sim)]}
assert all(path.is_relative_to(Path(sys.prefix)) for path in roots.values())
assert all('site-packages' in path.parts for path in roots.values())
data={}
for name in ('unilab-plc-sim','unilab-opcua-sim'):
    dist=metadata.distribution(name)
    data[name]={'version':dist.version,'requires':dist.requires,
        'entries':{entry.name:entry.value for entry in dist.entry_points if entry.group=='console_scripts'}}
assert data['unilab-plc-sim']['entries']['plc-sim']=='plc_sim.cli:main'
assert data['unilab-opcua-sim']['entries']['opcua-sim']=='opcua_sim.cli:main'
assert 'unilab-opcua-sim==0.2.6' in data['unilab-plc-sim']['requires']
print(json.dumps({'prefix':sys.prefix,'imports':{key:str(value) for key,value in roots.items()},'distributions':data}))
'''
    identity = json.loads(subprocess.check_output([str(interpreter), '-I', '-c', probe], text=True))
    results = []
    for name in ('plc-sim', 'opcua-sim'):
        executable = interpreter.parent / name
        for argument in ('--help', '--version', 'not-a-command'):
            result = subprocess.run([str(executable), argument], capture_output=True, text=True)
            expected = 2 if argument == 'not-a-command' else 0
            assert result.returncode == expected, (name, argument, result.stderr)
            results.append(dict(command=[str(executable), argument], exit=result.returncode,
                                stdout=result.stdout, stderr=result.stderr))
    for module in ('plc_sim', 'opcua_sim'):
        result = subprocess.run([str(interpreter), '-I', '-m', module, '--help'], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        results.append(dict(module=module, exit=result.returncode, stdout=result.stdout))
    args.output.write_text(json.dumps(dict(identity=identity, commands=results, passed=True,
        scope='isolated installed wheel imports, metadata, help/version/invalid-command routing; no GUI/server/hardware or native installer qualification'), indent=2))


if __name__ == '__main__':
    main()
