#!/bin/bash

plutil ~/Library/LaunchAgents/com.joemoser.runner.plist

launchctl unload ~/Library/LaunchAgents/com.joemoser.runner.plist

launchctl load -w ~/Library/LaunchAgents/com.joemoser.runner.plist

# Enable this plist
launchctl enable gui/501/com.joemoser.runner

# Run this plist now
launchctl kickstart gui/501/com.joemoser.runner
