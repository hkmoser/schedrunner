#!/bin/bash

launchctl unload ~/Library/LaunchAgents/com.joemoser.runner.plist

mv ~/Library/LaunchAgents/com.joemoser.runner.plist{,.disabled}

ls -al ~/Library/LaunchAgents/com.joemoser.runner.plist{,.disabled}
