"""Compile Safari AppleScript on macOS, without opening Safari or logging in."""
import ast
import plistlib
import subprocess
import tempfile
from pathlib import Path


def exercise(build):
    info = plistlib.loads((build / 'XHS-Agent.app/Contents/Info.plist').read_bytes())
    assert info.get('NSAppleEventsUsageDescription'), 'Missing Safari Automation permission description'
    tree = ast.parse((build / 'payload/backend/app/safari_capture.py').read_text('utf-8'))
    script = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                  and any(isinstance(name, ast.Name) and name.id == 'SCRIPT' for name in node.targets))
    with tempfile.TemporaryDirectory(prefix='xhs-safari-syntax-') as folder:
        source = Path(folder) / 'capture.applescript'; source.write_text(script, 'utf-8')
        subprocess.run(['/usr/bin/osacompile', '-o', str(Path(folder) / 'capture.scpt'), str(source)],
                       check=True, capture_output=True, text=True, timeout=30)
    return {'safari_applescript_compiles': True, 'safari_permission_description': True,
            'safari_real_account_login_tested': False}
