param([string]$ProjectDir = 'projects\windows-pilot')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$controllerPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$ttsPython = Join-Path $projectRoot '.venv-tts\Scripts\python.exe'
$uv = Join-Path $projectRoot '.venv\Scripts\uv.exe'
$modelRevision = '85e237c12c027371202489a0ec509ded67b5e4b5'
if (-not [System.IO.Path]::IsPathRooted($ProjectDir)) {
    $ProjectDir = Join-Path $projectRoot $ProjectDir
}
$ProjectDir = [System.IO.Path]::GetFullPath($ProjectDir)
if (-not (Test-Path -LiteralPath $controllerPython)) {
    throw 'Install the controller in .venv first; see docs/windows-quickstart.md.'
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectDir 'project.yaml'))) {
    throw 'Create the project with pla init before configuring Qwen.'
}

# Python and the TTS packages stay separate from Lemonade and the controller.
if (-not (Test-Path -LiteralPath $ttsPython)) {
    if (-not (Test-Path -LiteralPath $uv)) {
        & $controllerPython -m pip install 'uv==0.12.13'
        if ($LASTEXITCODE -ne 0) { throw 'uv installation failed.' }
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot 'tools\python'
    & $uv python install 3.12.14
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 installation failed.' }
    & $uv venv --python 3.12.14 --seed (Join-Path $projectRoot '.venv-tts')
    if ($LASTEXITCODE -ne 0) { throw 'TTS environment creation failed.' }
}
@'
import sys
assert sys.version_info[:2] == (3, 12), 'TTS requires Python 3.12'
'@ | & $ttsPython -
if ($LASTEXITCODE -ne 0) { throw 'The existing .venv-tts has the wrong Python version.' }
& $ttsPython -m pip install -r (Join-Path $projectRoot 'requirements-tts-windows.txt')
if ($LASTEXITCODE -ne 0) { throw 'Qwen or AMD runtime installation failed.' }
& $ttsPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'TTS dependencies are inconsistent.' }

@'
import torch
from qwen_tts import Qwen3TTSModel
assert torch.version.hip and torch.cuda.is_available(), 'AMD GPU unavailable'
print('TTS device:', torch.cuda.get_device_name(0))
value = torch.randn((256, 256), device='cuda:0', dtype=torch.bfloat16)
assert (value @ value).isfinite().all(), 'GPU computation failed'
torch.cuda.synchronize()
'@ | & $ttsPython -
if ($LASTEXITCODE -ne 0) { throw 'Qwen import or AMD GPU computation failed.' }

@'
import sys
from huggingface_hub import snapshot_download
print(snapshot_download('Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice', revision=sys.argv[1]))
'@ | & $ttsPython - $modelRevision
if ($LASTEXITCODE -ne 0) { throw 'Qwen model download failed.' }

@'
import sys
from pathlib import Path
from podcast_automate.storage import atomic_text, load_project, write_yaml
root, python, revision = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
config = load_project(root)
backup = root / 'reports/project-before-qwen.yaml'
if not backup.exists():
    atomic_text(backup, (root / 'project.yaml').read_text(encoding='utf-8'))
config.runtime.tts_python = python
config.runtime.tts_model = 'Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice'
config.runtime.tts_revision = revision
config.runtime.tts_device = 'cuda:0'
config.runtime.tts_attention = 'eager'
write_yaml(root / 'project.yaml', config.model_dump(mode='json'))
'@ | & $controllerPython - $ProjectDir $ttsPython $modelRevision
if ($LASTEXITCODE -ne 0) { throw 'Project configuration failed.' }
& $ttsPython (Join-Path $projectRoot 'src\podcast_automate\qwen_worker.py') --doctor --output (Join-Path $ProjectDir 'reports\qwen_environment.json')
if ($LASTEXITCODE -ne 0) { throw 'TTS environment report failed.' }
$installedPackages = & $ttsPython -m pip freeze
if ($LASTEXITCODE -ne 0) { throw 'Could not record TTS package versions.' }
$installedPackages | Set-Content -LiteralPath (Join-Path $ProjectDir 'reports\tts-requirements-installed.txt') -Encoding UTF8
Write-Output "Qwen configured for $ProjectDir. Use pla doctor, then audio-probe --approve-audio for the hearing test."
