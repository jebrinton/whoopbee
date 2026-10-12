#!/usr/bin/env python3
# <swiftbar.title>WHOOP · bedtime regularity</swiftbar.title>
# <swiftbar.version>1.0</swiftbar.version>
# <swiftbar.author>Yok</swiftbar.author>
# <swiftbar.desc>14-night fall-asleep sparkline in the menu bar; sleep timeline, HRV/RHR bands and behavior logging in the dropdown. Data from whoop_dash.py.</swiftbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))  # repo root
import whoop_dash
whoop_dash.menubar()
