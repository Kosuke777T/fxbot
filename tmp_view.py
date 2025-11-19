from pathlib import Path
lines=Path('app/core/mt5_client.py').read_text('utf-8', errors='replace').splitlines()
for i in range(120, 190):
    print(f'{i+1:03}: {lines[i]}')
