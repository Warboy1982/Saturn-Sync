## Saturn Sync
    
syncs a folder on your hard drive with storage on older model chitu boards

was developed for the original elegoo saturn, but probably works with the
mars line of printers as well.

## Use

Run the included .bat file or run "python Saturn_Sync_Final.py". This will start the program as a tray app. You may want to set this to run automatically on startup.

On first run, you will need to open the UI by right-clicking the tray icon, and then access the settings menu to setup your environment.

Once everything is configured, file syncing should begin automatically. From this point, user interaction is no longer necessary, simply save a .ctb file in the folder, and it'll appear on your printer.

The UI allows for issuing print commands, file deletion and provides real-time monitoring of uploads and print progress.

please note: the print progress is extremely inaccurate, as the printer provides progress reports in terms of bytes read. This is a terrible metric, as layer byte lengths are extremely inconsistent. In testing, the last 2 percent of the bytes in my print job represented 20 percent of the layers.

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

Change Sync Folder
```
Allows you to select a local folder to sync from.
```

Change Printer IP
```
Input your printer's IP address here. You may want to set a static IP for it in your router.
```

Set Ping Interval
```
When the printer is offline, ping every (n) minutes to establish connection.
```

Set Transfer Delay
```
Sets an (n) ms delay between sending file chunks to avoid transfer errors.
```
