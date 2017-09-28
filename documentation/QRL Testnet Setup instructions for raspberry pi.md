**WARNING: These instructions are outdated**

# Running a Quantum Resistant Ledger node on a Raspberry Pi

## Raspberry Pi operating system installation & setup : 

- Download latest Raspberry Pi operating system image : https://www.raspberrypi.org/downloads/raspbian/
- Install it using official Raspberry instructions :https://www.raspberrypi.org/documentation/installation/installing-images/README.md
- Change default pi password by opening a terminal and type the following command :

```passwd ```  
> default password is 'raspberry'. Enter a new password (twice)

- General setup to set locale, Time Zone, Hostname, in Adv Menu Expand Filesystem to match uSD). For more detail see : https://www.raspberrypi.org/documentation/configuration/raspi-config.md

```	sudo raspi-config ``` 

    
- If required, edit the network config file to set up a static IP address. For more details see : https://raspberrypi.stackexchange.com/questions/37920/how-do-i-set-up-networking-wifi-static-ip-address

```sudo nano /etc/network/interfaces``` 

        
- Get last updates :

```sudo apt update```

- Install firewall
```
sudo apt-get install ufw
```

- Setup firewall rules
```
sudo ufw allow OpenSSH
sudo ufw allow 9000/tcp
sudo ufw allow from 127.0.0.1 to 127.0.0.1 port 2000 proto tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing 
sudo ufw enable
```

- Check firewall status

```
sudo ufw status verbose
```

## QRL installation & setup
- Install python packages :

```sudo apt-get install python-dev```

- Type the following command to clone the repository :

```git clone https://github.com/theQRL/QRL.git```


- Install dependencies :

```
cd /home/pi/QRL
sudo pip3 install -r requirements.txt
```
  
  
## Running the node
- In the terminal, type the following commands :
```
cd /home/pi/QRL
python start_qrl.py
```

- If you've set it up correctly, it should start to output the following:
```
|unsynced| DEBUG : =====================================================================================
|unsynced| INFO : Data Path: /home/pi/.qrl/data
|unsynced| INFO : Wallet Path: /home/pi/.qrl/wallet
|unsynced| INFO : Initializing chain..
|unsynced| INFO : DB path: /home/pi/.qrl/data/state
|unsynced| INFO : Creating new wallet file... (this could take up to a minute)
```
After the wallet is created it will start synchronizing the chain.
This might take a while, leave it running until the chain is sync

- If you want to keep QRL running after disconnecting terminal, you have to launch it in background :

```nohup python start_qrl.py &```

## Check Sync process

- You can find the status of the sync process (synced, syncing or unsynced) in the QRL log :

```grep -i sync /home/pi/QRL/qrl.log | tail -1```

- Find last received blocks and compare it with QRL chain explorer http://qrlexplorer.info/

```grep -i "Received Block"  /home/pi/QRL/qrl.log | tail -1```

> Another way to get the last received block is to connect locally on the wallet (see below) and use command `blockheight`



## Check QRL memory usage

- Find QRL process :

```pgrep python```

- Check memory usage

```top -p <python process id>```

- Is this example, memory usage is : 418MB (945512 x 44.9%) :

```
pi@raspberrypi:$ pgrep python
23028
pi@raspberrypi:$ top -p 23028
top - 11:47:08 up 19:40,  4 users,  load average: 0.82, 0.95, 0.98
Tasks:   1 total,   1 running,   0 sleeping,   0 stopped,   0 zombie
%Cpu(s): 25.0 us,  0.1 sy,  0.0 ni, 74.9 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st
KiB Mem:    **945512** total,   873320 used,    72192 free,    29036 buffers
KiB Swap:   102396 total,     9240 used,    93156 free.   213964 cached Mem
PID USER      PR  NI    VIRT    RES    SHR S  %CPU %MEM     TIME+ COMMAND
23028 pi        20   0  441368 424340   3716 R  99.9 **44.9** 600:54.15 python
```


## Stopping the node
- It can be required to stop the node, specially during testnet. Type the following to kill python process.

```pkill python```

## Update the node

- First stop the python process (see above) and update the local git repository

```
cd /home/pi/QRL
git pull
```
- restart QRL

```
cd /home/pi/QRL
python start_qrl.py
```

## Accessing the wallet
- To access the wallet, you need telnet. Type the following command to install telnet :

`sudo apt-get install telnet`

- Run the following command to start the node :

`python start_qrl.py`

- Once it starts the synchronisation process, you can telnet into the node. Type the following command in the terminal :

`telnet localhost 2000`

> type `help` for the cmd list

## Launch the node automatically at startup
- In the system settings (Start - Preferences - Raspberry Pi Configuration), make sure the "Boot" option is set to "To Desktop". In GUI distributions this is already pre-configured.

- Make sure you have the `/home/pi/QRL/autostartQRL.sh` script with executable right

`ls -l /home/pi/QRL/autostartQRL.sh/home/pi/autostartQRL.sh`

- Add the script to the autostart folder (the location of the autostart file varies depending on your raspberry distribution) :

`nano /home/pi/.config/lxsession/LXDE-pi/autostart`

- Add the following line above(!) @xscreensaver -no-splash :

`@lxterminal -e /home/pi/QRL/autostartQRL.sh &`
Press ctrl+x to close, press y to save and press enter

- Make the python script executable :

`sudo chmodx [your folder]/QRL/main.py`

- See if it works!

## Launch the node automatically every night
- It can be useful to restart the node on a regular basis, specially during testnet

- Make sure you have the `/home/pi/QRL/autostartQRL.sh` script with executable right

`ls -l /home/pi/QRL/autostartQRL.sh`

- Edit the crontab to restart QRL automatically

`crontab -e`

- Append the following entry :

`43 6 * * * /home/pi/QRL/autostartQRL.sh`

> In this example, QRL is restarted every day at 6:43. Please change the time to whatever in order to avoid all nodes restart at same time !
