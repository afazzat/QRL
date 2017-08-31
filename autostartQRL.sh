#!/bin/bash
echo "Killing python..."
ps -ef | grep "python main.py" | grep -v grep | awk '{print $2}' | xargs kill -9

echo "Updating QRL..."
cd $HOME/QRL
git pull

echo "Updating QRL python dependencies..."
sudo pip install -r requirements.txt

echo "Restart QRL node"
./start_qrl

$SHELL
