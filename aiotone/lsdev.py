#!/usr/bin/env python3

from miniaudio import Backend, Devices
from rtmidi import MidiIn, MidiOut

print("- Audio devices")
for backend in Backend:
	try:
		devices = Devices([backend]).get_playbacks()
		print(f"  - {backend}")
		for device in devices:
			name = device["name"]
			print(f"    🔊 {name}")
	except:
		pass

print("- MIDI inputs")
for port in MidiIn().get_ports():
	print(f"  🎶 {port}")

print("- MIDI outputs")
for port in MidiOut().get_ports():
	print(f"  🎶 {port}")

print("""
Note that this script can display a lot of error or warning messages
from other libraries when trying to open backends that don't exist,
or when probing interfaces on some backends. Pay attention only to
the lines with 🔊 and 🎶!
""")
