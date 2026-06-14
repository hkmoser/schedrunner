#!/bin/bash

cp com.joemoser.runner.plist ~/Library/LaunchAgents/

launchctl unload ~/Library/LaunchAgents/com.joemoser.runner.plist

launchctl load -w ~/Library/LaunchAgents/com.joemoser.runner.plist

# Enable this plist
launchctl enable gui/501/com.joemoser.runner

# Run this plist now
launchctl kickstart gui/501/com.joemoser.runner

ls -al ~/Library/LaunchAgents/com.joemoser.runner.plist{,.disabled}
