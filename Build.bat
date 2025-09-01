pyinstaller --onefile -w --icon=.\res\printer_base.ico --add-data ".\res\printer_base.png;res" saturn_sync_full.py
del saturn_sync.exe
copy .\dist\saturn_sync_full.exe .\saturn_sync.exe
del .\dist\saturn_sync_full.exe