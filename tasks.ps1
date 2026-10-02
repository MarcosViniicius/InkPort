<#
    Atalhos para Windows (PowerShell).
    Uso:  .\tasks.ps1 install | run | dev | test | docker | clean
#>
param([Parameter(Position = 0)][string]$Task = "help")

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

switch ($Task) {
    "install"          { & $python -m pip install -r requirements.txt }
    "install-optional" { & $python -m pip install -r requirements-optional.txt }
    "run"              { & $python -m app }
    "dev"              { & $python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload }
    "test"             { & $python tests\smoke.py; & $python tests\conversions.py; & $python tests\rss_feeds.py; & $python tests\crosspoint_compat.py }
    "smoke"            { & $python tests\smoke.py }
    "convert"          { & $python tests\conversions.py }
    "rss"              { & $python tests\rss_feeds.py }
    "crosspoint"       { & $python tests\crosspoint_compat.py }
    "docker"           {
        $sha = (& git rev-parse HEAD 2>$null)
        if ($sha) { $env:GIT_SHA = $sha.Trim() }
        docker compose up -d --build
    }
    "network"          {
        & $python -c "from app.config import get_settings; s=get_settings(); print('bind:', f'{s.host}:{s.port}'); [print(' ', u) for u in s.access_urls]"
    }
    "firewall"         {
        $port = (& $python -c "from app.config import get_settings; print(get_settings().port)").Trim()
        Start-Process powershell -Verb RunAs -ArgumentList "-Command", "New-NetFirewallRule -DisplayName 'InkPort' -Direction Inbound -LocalPort $port -Protocol TCP -Action Allow"
    }
    "clean"            {
        Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force .pytest_cache, .ruff_cache -ErrorAction SilentlyContinue
    }
    default {
        Write-Host "Comandos: install | install-optional | run | dev | test | smoke | convert | rss | crosspoint | docker | network | firewall | clean"
    }
}
