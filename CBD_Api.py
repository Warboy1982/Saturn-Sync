import socket
from time import sleep
from datetime import datetime as dt
import os
from queue import Queue


class Printer():
    def __init__(self, ip) -> None:
        if ip == "127.0.0.1":
            self.debug = True
        self.ip = ip
        self.port = 3000
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) 
        self.sock.settimeout(3)
        self.buffSize = 4096
        self.jobs = Queue()
        self.send_delay = 0.005
        self.retries = 0
        self.remaining = 0
        self.filelength = 0
        
    def __sendRecieveSingle__(self,code,buffSize=-1) -> str: # sends an M-code then recieves a single packet answer
        self.sock.sendto(bytes(code, "utf-8"), (self.ip, self.port))
        if buffSize == -1:
            buffSize = self.buffSize
        try:
            output = self.sock.recv(buffSize)
        except: # maybe printer needed a sec longer, retry on fail
            output = self.sock.recv(buffSize)
        finally:
            return output
        
        
    def __clearBuffer__(self) -> None:
        output = ""
        while True:
            try:
                output = self.sock.recv(self.buffSize)
            except:
                break  # connection closed or no more data

    def __sendRecieveSingleNice__(self,code, buffSize=-1) -> str: # sends an M-code then recieves a single packet answer
        if buffSize == -1:
            buffSize = self.buffSize
        output = self.__stripFormatting__(self.__sendRecieveSingle__(code,buffSize))
        return output#.decode('utf-8')

    def __getUniversal__(self,split) -> str:
        output = (str)(self.__sendRecieveSingle__("M99999")).split(" ")[split].split(":")[1] # splits b'ok MAC:00:e0:4c:27:00:2e IP:192.168.1.174 VER:V1.4.1 ID:2e,00,27,00,17,50,53,54 NAME:CBD\r\n' into just a single field
        if not output:
            return "No Response"
        else:
            return output

    def getVer(self) -> str:
        """Returns the printers version

        Returns:
            str: Version
        """
        return self.__getUniversal__(3)
        
    def getID(self) -> str:
        """Returns Printers UID

        Returns:
            str: UID
        """
        return self.__getUniversal__(4)

    def getName(self) -> str:
        """Gets the printers Name

        Returns:
            str: Name
        """
        return self.__getUniversal__(5).split("\\")[0]

    def __stripFormatting__(self, string) -> str: # trims b'End file list\r\n' to End file list
        string = (string.decode("utf-8"))
        string = string.rstrip()
        return string

    def __stripSpaceFromBack__(self, string):
        exts = [".ctb", ".goo"]
        lower_string = string.lower()
        ext_index = max(lower_string.rfind(ext) for ext in exts)
        ext = next(ext for ext in exts if lower_string.rfind(ext) == ext_index)
        return string[:ext_index + len(ext)], string[ext_index + len(ext):].strip()

    def getCardFiles(self) -> list:
        """Returns the list of CTB files on the storage

        Returns:
            list: (filename, size)
        """
        self.sock.sendto(bytes("M20", "utf-8"), (self.ip, self.port))
        output = []
        request = self.__stripFormatting__((self.sock.recv(self.buffSize)))

        while request != "End file list":
            if ".ctb" in request.lower() or ".goo" in request.lower():
                if request != "Begin file list" and self.__stripSpaceFromBack__(request)[1] != 0: # this prevents deleted files from appearing
                    #output.append(request)
                    output.append(self.__stripSpaceFromBack__(request))

            request = self.__stripFormatting__((self.sock.recv(self.buffSize)))
        confirmation = self.sock.recv(self.buffSize) #absorb the ok message
        return(output)
    
    def homeAxis(self) -> None:
        """Homes Z axis
        """
        self.__sendRecieveSingle__("G28 Z")

    def getAxis(self) -> float:
        """Gets current Axis position

        Returns:
            float: current Z pos
        """
        pos = (float)((str)(self.__sendRecieveSingle__("M114")).split(" ")[4].strip("Z:"))
        return pos

    def jogHard(self,distance) -> None: # uses absolute pos
        """Jogs without checking machine softlimits (not recommmended)

        Args:
            distance (float): absolute position to move to
        """
        self.__sendRecieveSingle__("G0 Z"+ (str)(distance))

    def jogSoft(self,distance) -> str: # uses absolute pos
        """Jogs after checking machine softlimits

        Args:
            distance (float): absolute position to move to

        Returns:
            str: If move possible
        """
        if(distance < 200 or distance < 1):
            self.jogHard(distance)
            return "Complete"
        else:
            return "Distance too great or other error"

    def removeCardFile(self,filename) -> str:
        """Removes specified file from storage

        Args:
            filename (str): filename to remove including extension

        Returns:
            str: If is action complete
        """
        output = (str)(self.__sendRecieveSingleNice__("M30 "+filename))
        confirmation = self.sock.recv(self.buffSize) #absorb the ok message
        return(output)

    def startPrinting(self,filename) -> str:
        """Starts printing from storage

        Args:
            filename (str): what do you think? Must include extension

        Returns:
            str: If is action complete
        """
        return self.__sendRecieveSingleNice__(f"M6030 '{filename}'")
    
    def printingStatus(self) -> str:
        """Returns if the machine is printing

        Returns:
            str: Machine State
        """
        try:
            string = self.__sendRecieveSingleNice__("M27")
        except:
            return "Not Printing"

        confirmation = self.sock.recv(self.buffSize) #absorb the ok message
        if string.split()[0] == "SD":
            return f"Printing {string}"
        return "Not Printing"

    def printingPercent(self) -> list:
        """Returns the percentage of the print in bytes complete (not massively accurate)

        Returns:
            list: [completed, total]
        """
        string = self.__sendRecieveSingleNice__("M27")
        return string.split()[3].split("/")

    def stopPrinting(self) -> str:
        """Stops current print

        Returns:
            str: If completed
        """
        return self.__sendRecieveSingleNice__("M33")


    # upload structure
    # M28 [filename]
    # send data in 1280 chunks
    # M4012 I1 T[total bytes sent]
    # M29
    # all of this is encoded

    def uploadFile(self,fileNameLocal,fileNameCard="") -> str:
        """Uploads file to storage

        Args:
            fileNameLocal (str): local filename including extension
            fileNameCard (str, optional): filename on storage including extension. Defaults to same as local filename.

        Returns:
            str: If completed
        """
        if fileNameCard == "": fileNameCard = fileNameLocal
        
        # start transmission
        m28 = self.__sendRecieveSingleNice__(f"M28 {fileNameCard}")
        if "Error" in m28 or "Failed" in m28:
            confirmation = self.sock.recv(self.buffSize) #absorb the ok message after our error message
            return f"M28 Error: {m28}"
        
        self.filelength=os.stat(fileNameLocal).st_size
        f=open(fileNameLocal,'rb')
        self.remaining=self.filelength
        offs=0
        retr=0
        print(fileNameCard,' Length:',self.filelength)
        send = True
        readamt = 1280
        while self.remaining > 0:
            if send:
                f.seek(offs)
                dd=f.read(readamt)
                dc=bytearray(offs.to_bytes(length=4, byteorder='little'))
                cxor=0
                for c in dd: cxor=cxor ^ c
                for c in dc: cxor=cxor ^ c
                dc.append(cxor)
                dc.append(0x83)
                self.sock.sendto(dd+dc, (self.ip,self.port))
            s = self.sock.recv(self.buffSize)
            if s.split()[0] == b"ok":
                readamt = 1280
                if send:
                    offs=offs+len(dd)
                    self.remaining -= len(dd)
                else:
                    send = True
            elif s.split()[0] == b"resend":
                # example: b'resend 1280,offset error:6165760'
                s_str = s.decode("utf-8")
                parts = s_str.split()
                amt_str = parts[1].split(",")[0]
                offs_str = parts[2].split(":")[1]
                #readamt = int(amt_str)
                offs = int(offs_str)
                self.remaining = self.filelength - offs
                retr += 1
                send = True
            else:
                send = False # garbage message? 
            print(retr,self.remaining,end='   \r')
            sleep(self.send_delay)
        f.close()

        self.filelength = 0
        self.remaining = 0

        retstring = ""
        M4012 = self.__sendRecieveSingleNice__(f"M4012 I1 T{self.filelength}")
        if M4012.split()[0] != "ok":
            if retr > 0:
                retstring = f"{retr} Transfer Error(s): Consider increasing send delay.\n"
            retstring = retstring + f"Size Verify Error: {M4012}"
            confirmation = self.sock.recv(self.buffSize) #absorb the ok message after our error message
            return retstring

        retstring = self.__sendRecieveSingleNice__("M29")
        sleep(0.005)
        confirmation = self.sock.recv(self.buffSize)
        return retstring
    
    def formatCard(self):
        """Formats storage
        """
        for file in self.getCardFiles():
            self.removeCardFile(file[0])
    
def main():
    ip = input("Enter printer IP address (e.g. 192.168.1.174): ").strip()
    p = Printer(ip)

    menu = """
Choose an option:
1. List files on storage
2. Upload file
3. Delete file
4. Start print
5. Stop print
6. Get printing status
7. Get printing percentage
8. Get printer version
9. Get printer ID
10. Get printer name
11. Home Z axis
12. Get Z axis position
13. Jog Z axis (soft limits)
14. Format card (delete all files)
0. Exit
"""

    while True:
        print(menu)
        choice = input("Enter choice: ").strip()
        if choice == "1":
            files = p.getCardFiles()
            if files:
                print("Files on card (filename, size):")
                for f in files:
                    print(f"  {f[0]}  {f[1]} bytes")
            else:
                print("No files found.")
        elif choice == "2":
            local_file = input("Enter local filename to upload (with extension): ").strip()
            card_file = input("Enter filename on card (leave empty to use same): ").strip()
            try:
                res = p.uploadFile(local_file, card_file)
                print(f"Upload result: {res}")
            except FileNotFoundError:
                print("Local file not found.")
            except Exception as e:
                print(f"Error during upload: {e}")
        elif choice == "3":
            filename = input("Enter filename to delete (with extension): ").strip()
            res = p.removeCardFile(filename)
            print(f"Delete result: {res}")
        elif choice == "4":
            filename = input("Enter filename to print (with extension): ").strip()
            res = p.startPrinting(filename)
            print(f"Start print result: {res}")
        elif choice == "5":
            res = p.stopPrinting()
            print(f"Stop print result: {res}")
        elif choice == "6":
            status = p.printingStatus()
            print(f"Printing status: {status}")
        elif choice == "7":
            try:
                percent = p.printingPercent()
                print(f"Printed bytes: {percent[0]} / {percent[1]}")
            except Exception as e:
                print(f"Error getting print percentage: {e}")
        elif choice == "8":
            ver = p.getVer()
            print(f"Printer version: {ver}")
        elif choice == "9":
            pid = p.getID()
            print(f"Printer ID: {pid}")
        elif choice == "10":
            name = p.getName()
            print(f"Printer name: {name}")
        elif choice == "11":
            p.homeAxis()
            print("Z axis homed.")
        elif choice == "12":
            try:
                pos = p.getAxis()
                print(f"Current Z axis position: {pos}")
            except Exception as e:
                print(f"Error getting axis position: {e}")
        elif choice == "13":
            try:
                dist = float(input("Enter Z position to jog to (soft limits apply): ").strip())
                res = p.jogSoft(dist)
                print(res)
            except ValueError:
                print("Invalid number.")
        elif choice == "14":
            confirm = input("Are you sure you want to format card and delete all files? (yes/no): ").strip().lower()
            if confirm == "yes":
                p.formatCard()
                print("Card formatted (all files deleted).")
            else:
                print("Format cancelled.")
        elif choice == "0":
            print("Exiting.")
            exit(0)
        else:
            print("Invalid choice, try again.")

if __name__ == "__main__":
    main()
