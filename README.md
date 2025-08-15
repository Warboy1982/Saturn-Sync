## Saturn Sync
    
syncs a folder on your hard drive with storage on older model chitu boards

was developed for the original elegoo saturn, but probably works with the
mars line of printers as well.

.ctb files are automatically transferred without user interaction.

any additions, deletions or updates to .ctb files in the local folder will also happen on your printer's usb stick.

allows remote triggering of print jobs.


runs as a tray app, with an openable UI for checking sync status, sending print jobs, configuring the local folder, the printer IP, and the ping interval for establishing a connection.

## Installing

Make sure you have python installed:

https://www.python.org/

grab the dependencies:

Windows
```python
pip install -r requirements.txt
```
MacOS/ Linux
```python
pip3 install -r requirements.txt
```

## Configuration
On first run, you will need to open the UI by right-clicking the tray icon, and then access the settings menu to setup your environment.

Change Sync Folder
```
Allows you to select a local folder on your hard drive to sync with the printer
```

Change Printer IP
```
Input your printer's IP address here. You may want to set a static ip for the printer for consistency between runs.
```

Set Ping Interval
```
When the printer is offline, ping every (n) minutes to establish connection. When connection is established, syncing will occur.
```

Set Transfer Delay
```
Sets a delay between sending chunks of files when syncing to avoid flooding the network with traffic and minimize transfer errors.
```

## Use

Run the included .bat file or run "python Saturn_Sync_Final.py". You may want to set this to run automatically on startup.

Once everything is configured, file syncing should begin automatically. From this point, user interaction is no longer necessary, simply save a .ctb file in the folder, and it'll appear on your printer.

The UI allows for issuing print commands, file deletion and provides real-time monitoring of uploads and print progress.

please note: the print progress is extremely inaccurate, as the printer provides progress reports in terms of bytes read. This is a terrible metric, as layer byte lengths are extremely inconsistent. In testing, the last 2 percent of the bytes in my print job represented 20 percent of the layers.